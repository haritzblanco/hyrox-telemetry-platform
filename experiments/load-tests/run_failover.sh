#!/usr/bin/env bash
# Caída de una réplica en mitad del pico de diseño.
#
# La matriz de escalabilidad mide configuraciones estables y concluye que una
# sola réplica absorbe el pico. De ahí se sigue una pregunta que la matriz no
# responde: si una basta, ¿para qué la segunda? La respuesta es la
# disponibilidad, y esta corrida la mide en vez de afirmarla. Con dos réplicas
# repartiendo el pico se elimina una a mitad de corrida y se observa qué le
# ocurre al caudal, a la latencia y a la pérdida mientras la superviviente
# absorbe el total y el planificador repone la que falta.
#
# A diferencia de run_peak.sh, el número de réplicas se congela: si mandara
# KEDA, el reemplazo se confundiría con una decisión de escalado y no se
# sabría qué parte del resultado es tolerancia a fallos.
#
# Requisitos: los mismos que run_matrix.sh (ventana de experimento abierta,
# relojes sincronizados, --metrics-interval activo).
#
# Variables (todas con default):
#   N=40 SPEEDUP=20            carga ≈ N × SPEEDUP msg/s durante ~230 s
#   REPLICAS=2                 réplicas congeladas durante toda la corrida
#   KILL_AT=90                 segundos desde el inicio de la carga hasta la baja
#   CPU_LIMIT=""               límite de CPU por réplica (ver run_matrix.sh)
#   POLL=5                     periodo de muestreo de réplicas y recursos
set -uo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

N="${N:-40}"
SPEEDUP="${SPEEDUP:-20}"
REPLICAS="${REPLICAS:-2}"
KILL_AT="${KILL_AT:-90}"
POLL="${POLL:-5}"
CPU_LIMIT="${CPU_LIMIT:-}"
BROKER_HOST="${BROKER_HOST:-192.168.252.2}"
BROKER_PORT="${BROKER_PORT:-31883}"
INFLUX_URL="${INFLUX_URL:-http://192.168.252.2:30086}"
INFLUX_ORG="${INFLUX_ORG:-hyrox}"
INFLUX_BUCKET="${INFLUX_BUCKET:-telemetry}"
NS="${NS:-hyrox}"
SELECTOR="app.kubernetes.io/name=processor"
CA_FILE="${CA_FILE:-$ROOT/infra/mqtt/ca.crt}"

INFLUX_TOKEN="${INFLUX_TOKEN:-$(kubectl get secret influxdb-auth -n "$NS" -o jsonpath='{.data.token}' | base64 -d)}"
DEVICE_PASSWORD="${DEVICE_PASSWORD:-$(kubectl get secret device-broker -n "$NS" -o jsonpath='{.data.password}' | base64 -d)}"

SIM="$ROOT/components/simulator/.venv/bin/hyrox-sim"
STAMP="$(date -u +%Y%m%d_%H%M%S)"
OUTDIR="${OUTDIR:-$ROOT/experiments/results/${STAMP}_failover}"
mkdir -p "$OUTDIR"

RATE=$(( N * SPEEDUP ))
SESSION="failover_$(date -u +%H%M%S)"

[[ -x "$SIM" ]] || { echo "ERROR: no encontrado $SIM (¿venv del simulador?)"; exit 1; }
kubectl get deployment/processor -n "$NS" >/dev/null || exit 1
if ! kubectl get deployment/processor -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].args}' \
        | grep -q -- '--metrics-interval'; then
    echo "ERROR: el Deployment no tiene --metrics-interval (ventana de experimento cerrada)"; exit 1
fi

echo "=== Caída de una réplica en el pico de diseño ==="
echo "Carga: $N atletas × speedup $SPEEDUP ≈ $RATE msg/s | sesión $SESSION"
echo "Réplicas congeladas en $REPLICAS | baja a los ${KILL_AT}s"
[[ -n "$CPU_LIMIT" ]] && echo "Límite de CPU por réplica: $CPU_LIMIT"
echo "Resultados en: $OUTDIR"
echo ""

echo "Sincronizando relojes de las VMs..."
bash "$ROOT/experiments/load-tests/clock_sync.sh" | tee "$OUTDIR/clock_offset.txt"
echo ""

# ── límite de CPU y congelación del escalado ────────────────────────────────
CPU_LIMIT_ORIG=""
if [[ -n "$CPU_LIMIT" ]]; then
    CPU_LIMIT_ORIG="$(kubectl get deployment/processor -n "$NS" \
        -o jsonpath='{.spec.template.spec.containers[0].resources.limits.cpu}')"
    kubectl patch deployment/processor -n "$NS" --type=json -p "[
        {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/limits/cpu\",\"value\":\"$CPU_LIMIT\"},
        {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/requests/cpu\",\"value\":\"$CPU_LIMIT\"}]" >/dev/null
fi

restaurar() {
    if kubectl get scaledobject/processor -n "$NS" >/dev/null 2>&1; then
        kubectl annotate scaledobject/processor -n "$NS" \
            autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true
    fi
    if [[ -n "$CPU_LIMIT_ORIG" ]]; then
        kubectl patch deployment/processor -n "$NS" --type=json -p "[
            {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/limits/cpu\",\"value\":\"$CPU_LIMIT_ORIG\"},
            {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/requests/cpu\",\"value\":\"200m\"}]" >/dev/null 2>&1 || true
    fi
    pkill -P $$ -f "kubectl logs" 2>/dev/null || true
}
trap restaurar EXIT

if kubectl get scaledobject/processor -n "$NS" >/dev/null 2>&1; then
    kubectl annotate scaledobject/processor -n "$NS" \
        "autoscaling.keda.sh/paused-replicas=$REPLICAS" --overwrite >/dev/null
fi
kubectl scale deployment/processor -n "$NS" --replicas="$REPLICAS" >/dev/null
kubectl rollout status deployment/processor -n "$NS" --timeout=180s
sleep 6

# ── seguimiento de logs ─────────────────────────────────────────────────────
# El pod que se elimina se lleva sus logs consigo, así que no vale con leerlos
# al final como en run_matrix.sh: hay que ir siguiéndolos desde el principio.
# El mismo vigilante engancha al pod de reemplazo en cuanto aparece.
seguir_logs() {
    local seguidos=" "
    while true; do
        for pod in $(kubectl get pods -n "$NS" -l "$SELECTOR" \
                     -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
            if [[ "$seguidos" != *" $pod "* ]]; then
                seguidos+="$pod "
                kubectl logs -f "$pod" -n "$NS" --since=10s \
                    >> "$OUTDIR/raw_${pod}.log" 2>/dev/null &
            fi
        done
        sleep 2
    done
}

sample_replicas() {
    local out="$1"
    echo "ts,ready_replicas,pods" > "$out"
    while true; do
        local ready pods
        ready="$(kubectl get deployment/processor -n "$NS" -o jsonpath='{.status.readyReplicas}' 2>/dev/null)"
        pods="$(kubectl get pods -n "$NS" -l "$SELECTOR" \
                --field-selector=status.phase=Running \
                -o jsonpath='{.items[*].metadata.name}' 2>/dev/null | tr ' ' '|')"
        echo "$(date -u +%H:%M:%S),${ready:-0},${pods}" >> "$out"
        sleep "$POLL"
    done
}

bash "$ROOT/experiments/load-tests/record_placement.sh" "$OUTDIR/placement.csv"
seguir_logs & LOGS_PID=$!
sample_replicas "$OUTDIR/replicas.csv" & REPL_PID=$!
bash "$ROOT/experiments/load-tests/sample_resources.sh" "$OUTDIR/resources.csv" "$POLL" & SAMPLER_PID=$!

# ── la corrida ──────────────────────────────────────────────────────────────
START_ISO="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
: > "$OUTDIR/sim.jsonl"
"$SIM" --athletes "$N" --athlete-prefix atleta --session-id "$SESSION" \
       --broker-host "$BROKER_HOST" --broker-port "$BROKER_PORT" \
       --broker-password "$DEVICE_PASSWORD" --broker-ca "$CA_FILE" \
       --speedup "$SPEEDUP" --seed 42 --log-level WARNING \
       >> "$OUTDIR/sim.jsonl" 2>"$OUTDIR/sim.log" &
SIM_PID=$!

# ── la baja ─────────────────────────────────────────────────────────────────
sleep "$KILL_AT"
VICTIMA="$(kubectl get pods -n "$NS" -l "$SELECTOR" --field-selector=status.phase=Running \
           -o jsonpath='{.items[0].metadata.name}')"
KILL_ISO="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
echo "── baja de $VICTIMA a los ${KILL_AT}s ($KILL_ISO) ──"
# --grace-period=0 --force: una caída, no un apagado ordenado. Con terminación
# ordenada el pod se despide del broker y la corrida mediría un relevo limpio,
# que es justo el caso fácil.
kubectl delete pod "$VICTIMA" -n "$NS" --grace-period=0 --force >/dev/null 2>&1

wait "$SIM_PID" 2>/dev/null || true
sleep 5
END_ISO="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

kill "$SAMPLER_PID" "$REPL_PID" 2>/dev/null
wait "$SAMPLER_PID" "$REPL_PID" 2>/dev/null
sleep 2
kill "$LOGS_PID" 2>/dev/null; wait "$LOGS_PID" 2>/dev/null
pkill -P $$ -f "kubectl logs" 2>/dev/null || true

# ── métricas por réplica, incluida la que ya no existe ──────────────────────
for raw in "$OUTDIR"/raw_*.log; do
    [[ -e "$raw" ]] || continue
    pod="$(basename "$raw" .log)"; pod="${pod#raw_}"
    python3 "$ROOT/experiments/load-tests/collect_metrics.py" \
        --start "$START_ISO" --end "$END_ISO" \
        < "$raw" > "$OUTDIR/proc_${pod}.jsonl" 2>>"$OUTDIR/collect.log"
done

# ── contabilidad ────────────────────────────────────────────────────────────
influx_count() {
    local flux="from(bucket:\"$INFLUX_BUCKET\")
      |> range(start: -2d, stop: 1d)
      |> filter(fn:(r) => r._measurement==\"biometrics\" and r.session_id==\"$SESSION\" and r._field==\"heart_rate\")
      |> group()
      |> count()"
    curl -s -m 60 --request POST "$INFLUX_URL/api/v2/query?org=$INFLUX_ORG" \
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

COUNTS="$(python3 -c "
import json
enq=ack=0
for l in open('$OUTDIR/sim.jsonl'):
    l=l.strip()
    if not l: continue
    try:
        d=json.loads(l); enq+=d.get('published',0); ack+=d.get('acked', d.get('published',0))
    except Exception: pass
print(enq, ack)")"
read -r ENQUEUED OFFERED <<< "$COUNTS"
DELIVERED="$(influx_count)"

python3 -c "
import json
json.dump({'run':'failover','athletes':$N,'speedup':$SPEEDUP,'target_rate_msg_s':$RATE,
           'replicas':$REPLICAS,'cpu_limit':'$CPU_LIMIT' or None,
           'killed_pod':'$VICTIMA','kill_at_s':$KILL_AT,'kill_ts':'$KILL_ISO',
           'session':'$SESSION','start':'$START_ISO','end':'$END_ISO',
           'enqueued':$ENQUEUED,'offered':$OFFERED,'delivered':$DELIVERED},
          open('$OUTDIR/run.json','w'), indent=2)"

echo ""
echo "encolado=$ENQUEUED  ofrecido(acked)=$OFFERED  persistido=$DELIVERED  pérdida=$(( OFFERED - DELIVERED ))"
echo "Réplica dada de baja: $VICTIMA"
echo ""
echo "Analiza la corrida con:"
echo "    python3 experiments/load-tests/analyze_peak.py $OUTDIR"
