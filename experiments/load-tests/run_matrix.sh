#!/usr/bin/env bash
# Orquestador de la evaluación experimental: ejecuta la matriz
#   réplicas del processor  ×  carga ofrecida (nº de atletas)
# y recoge, por corrida, las métricas del processor (latencia/throughput),
# el consumo de recursos y la pérdida (ofrecido vs persistido).
#
# El procesado lo hace SIEMPRE el clúster (no se arranca processor local). La
# carga la genera el simulador del proyecto: N atletas que publican a 1 lectura
# por segundo de carrera acelerada `speedup` → tasa ≈ N × speedup msg/s.
#
# Requisitos: VMs arrancadas, imagen hyrox/processor:0.3.0 importada, Deployment
# de experimento aplicado (experiments/load-tests/processor-exp.yaml) y
# metrics-server operativo. Ver experiments/README.md.
#
# Variables (todas con default):
#   REPLICAS="1 2 4"           lista de nº de réplicas del processor
#   N_LIST="4 8 12 16 20"      lista de nº de atletas (carga)
#   SPEEDUP=50                 aceleración temporal → tasa = N × SPEEDUP msg/s
#   BROKER_HOST=192.168.252.2  BROKER_PORT=31883
#   INFLUX_URL=http://192.168.252.2:30086   INFLUX_TOKEN=<del Secret del clúster>
#   SETTLE=6                   segundos de asentamiento tras escalar
set -uo pipefail

cd "$(dirname "$0")/../.."          # raíz del repo
ROOT="$(pwd)"

REPLICAS="${REPLICAS:-1 2 4}"
N_LIST="${N_LIST:-4 8 12 16 20}"
SPEEDUP="${SPEEDUP:-50}"
BROKER_HOST="${BROKER_HOST:-192.168.252.2}"
BROKER_PORT="${BROKER_PORT:-31883}"
INFLUX_URL="${INFLUX_URL:-http://192.168.252.2:30086}"
INFLUX_TOKEN="${INFLUX_TOKEN:-$(kubectl get secret influxdb-auth -n hyrox -o jsonpath='{.data.token}' | base64 -d)}"
INFLUX_ORG="${INFLUX_ORG:-hyrox}"
INFLUX_BUCKET="${INFLUX_BUCKET:-telemetry}"
NS="${NS:-hyrox}"
SELECTOR="app.kubernetes.io/name=processor"
SETTLE="${SETTLE:-6}"

SIM="$ROOT/components/simulator/.venv/bin/hyrox-sim"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
OUTDIR="$ROOT/experiments/results/$STAMP"
mkdir -p "$OUTDIR"

# ── comprobaciones previas ──────────────────────────────────────────────────
[[ -x "$SIM" ]] || { echo "ERROR: no encontrado $SIM (¿venv del simulador?)"; exit 1; }
command -v kubectl >/dev/null || { echo "ERROR: kubectl no disponible"; exit 1; }
kubectl get deployment/processor -n "$NS" >/dev/null 2>&1 || {
    echo "ERROR: no existe el Deployment processor en $NS."
    echo "       Aplica primero: kubectl apply -f experiments/load-tests/processor-exp.yaml"; exit 1; }
# La instrumentación debe estar activa en el Deployment.
if ! kubectl get deployment/processor -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].args}' \
        | grep -q -- '--metrics-interval'; then
    echo "ERROR: el Deployment no tiene --metrics-interval (¿aplicaste processor-exp.yaml?)"; exit 1
fi
kubectl top pods -n "$NS" >/dev/null 2>&1 || echo "AVISO: 'kubectl top' falla; resources.csv saldrá vacío (¿metrics-server?)"

echo "=== Evaluación experimental ==="
echo "Réplicas: $REPLICAS | Atletas: $N_LIST | speedup: $SPEEDUP → tasa ≈ N×$SPEEDUP msg/s"
echo "Resultados en: $OUTDIR"
echo ""

# ── sincronización de reloj (offset Mac↔VM para la latencia de transporte) ──
echo "Sincronizando relojes de las VMs..."
bash "$ROOT/experiments/load-tests/clock_sync.sh" | tee "$OUTDIR/clock_offset.txt"
echo ""

# ── consulta de pérdida: lecturas persistidas para una sesión ───────────────
influx_count() {
    local session="$1"
    # group() colapsa todas las series (athlete×phase…) en una sola tabla ANTES de
    # count(), de modo que el conteo sea el TOTAL de lecturas de la sesión (un punto
    # por lectura = un valor de heart_rate). Sin group(), count() cuenta por serie.
    local flux="from(bucket:\"$INFLUX_BUCKET\")
      |> range(start: -2d, stop: 1d)
      |> filter(fn:(r) => r._measurement==\"biometrics\" and r.session_id==\"$session\" and r._field==\"heart_rate\")
      |> group()
      |> count()"
    curl -s -m 30 --request POST "$INFLUX_URL/api/v2/query?org=$INFLUX_ORG" \
        --header "Authorization: Token $INFLUX_TOKEN" \
        --header "Accept: application/csv" \
        --header "Content-Type: application/vnd.flux" \
        --data "$flux" 2>/dev/null | \
    python3 -c "import sys,csv
tot=0
for r in csv.reader(sys.stdin):
    if r and r[-1].strip().isdigit():
        tot=int(r[-1])
print(tot)" 2>/dev/null || echo 0
}

# ── una corrida (R réplicas, N atletas) ─────────────────────────────────────
run_one() {
    local R="$1" N="$2"
    local run="R${R}_N${N}"
    local rundir="$OUTDIR/$run"
    mkdir -p "$rundir"
    local session="exp_${run}_$(date -u +%H%M%S)"
    local rate=$(( N * SPEEDUP ))

    echo "── $run  (≈ $rate msg/s, sesión $session) ──────────────"

    local start_iso; start_iso="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

    # muestreo de recursos en segundo plano
    bash "$ROOT/experiments/load-tests/sample_resources.sh" "$rundir/resources.csv" 2 &
    local sampler_pid=$!

    # N atletas en UN proceso (una conexión MQTT por atleta): el broker ve la
    # misma topología de clientes, pero el Mac no paga N intérpretes de Python
    # compitiendo con las VMs por los núcleos físicos. stdout = una línea JSON
    # por atleta con lo publicado/confirmado.
    : > "$rundir/sim.jsonl"
    "$SIM" --athletes "$N" --athlete-prefix atleta --session-id "$session" \
           --broker-host "$BROKER_HOST" --broker-port "$BROKER_PORT" \
           --speedup "$SPEEDUP" --seed 42 --log-level WARNING \
           >> "$rundir/sim.jsonl" 2>/dev/null &
    local sim_pid=$!
    wait "$sim_pid" 2>/dev/null || true

    # margen para que el último lote se confirme en InfluxDB (flush_interval 0,5 s)
    sleep 3
    local end_iso; end_iso="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

    kill "$sampler_pid" 2>/dev/null || true; wait "$sampler_pid" 2>/dev/null || true

    # métricas del processor por pod, acotadas a la ventana de la corrida
    for pod in $(kubectl get pods -n "$NS" -l "$SELECTOR" -o jsonpath='{.items[*].metadata.name}'); do
        python3 "$ROOT/experiments/load-tests/collect_metrics.py" \
            --pod "$pod" --namespace "$NS" --start "$start_iso" --end "$end_iso" \
            > "$rundir/proc_${pod}.jsonl" 2>>"$rundir/collect.log"
    done

    # ofrecido (confirmado por el broker: suma de acked) y persistido (InfluxDB).
    # `published` (encolado en el cliente) se guarda aparte: su diferencia con
    # acked es saturación del generador, no pérdida de la plataforma.
    local counts; counts="$(python3 -c "
import sys,json
enq=ack=0
for l in open('$rundir/sim.jsonl'):
    l=l.strip()
    if not l: continue
    try:
        d=json.loads(l)
        enq+=d.get('published',0)
        ack+=d.get('acked', d.get('published',0))
    except Exception: pass
print(enq, ack)")"
    local enqueued offered
    read -r enqueued offered <<< "$counts"
    local delivered; delivered="$(influx_count "$session")"

    python3 -c "
import json
json.dump({'run':'$run','replicas':$R,'athletes':$N,'speedup':$SPEEDUP,
           'target_rate_msg_s':$rate,'session':'$session',
           'start':'$start_iso','end':'$end_iso',
           'enqueued':$enqueued,'offered':$offered,'delivered':$delivered},
          open('$rundir/run.json','w'), indent=2)"
    echo "   encolado=$enqueued  ofrecido(acked)=$offered  persistido=$delivered  (pérdida=$(( offered - delivered )))"
    echo ""
}

# ── matriz ──────────────────────────────────────────────────────────────────
for R in $REPLICAS; do
    echo "### Escalando processor a $R réplica(s) ###"
    kubectl scale deployment/processor -n "$NS" --replicas="$R" >/dev/null
    kubectl rollout status deployment/processor -n "$NS" --timeout=120s
    sleep "$SETTLE"
    for N in $N_LIST; do
        run_one "$R" "$N"
    done
done

echo "=== Matriz completada. Analiza con: ==="
echo "    python experiments/notebooks/analyze.py $OUTDIR"
