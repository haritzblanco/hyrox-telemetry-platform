#!/usr/bin/env bash
# Corrida única al pico de diseño con la CONFIGURACIÓN DE PRODUCCIÓN.
#
# A diferencia de run_matrix.sh, que fija el número de réplicas para barrer la
# matriz, aquí no se toca el escalado: manda el ScaledObject de KEDA, igual que
# en un evento real. La corrida mide a la vez las tres magnitudes que el
# capítulo de requisitos acota con umbral:
#   RNF-1  el escalado sigue al caudal (línea temporal de réplicas)
#   RNF-2  latencia emisión → dato consultable (transporte + persistencia)
#   RNF-3  pérdida = ofrecido (PUBACK del broker) − persistido (InfluxDB)
#
# Requisitos: VMs arrancadas, relojes sincronizados (clock_sync.sh) y el
# Deployment de producción con --metrics-interval activo (ver README: ventana
# de experimento con la sincronía de ArgoCD suspendida).
#
# Variables (todas con default):
#   N=40 SPEEDUP=20            carga ≈ N × SPEEDUP msg/s durante ~4 min
#   POLL=5                     periodo de muestreo de réplicas y recursos
set -uo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

N="${N:-40}"
SPEEDUP="${SPEEDUP:-20}"
POLL="${POLL:-5}"
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
OUTDIR="${OUTDIR:-$ROOT/experiments/results/${STAMP}_peak}"
mkdir -p "$OUTDIR"

RATE=$(( N * SPEEDUP ))
SESSION="peak_$(date -u +%H%M%S)"

# ── comprobaciones previas ──────────────────────────────────────────────────
[[ -x "$SIM" ]] || { echo "ERROR: no encontrado $SIM (¿venv del simulador?)"; exit 1; }
kubectl get deployment/processor -n "$NS" >/dev/null || exit 1
if ! kubectl get deployment/processor -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].args}' \
        | grep -q -- '--metrics-interval'; then
    echo "ERROR: el Deployment de producción no tiene --metrics-interval."
    echo "       Abre la ventana de experimento antes de lanzar la corrida."; exit 1
fi
kubectl get scaledobject/processor -n "$NS" >/dev/null || {
    echo "ERROR: no hay ScaledObject: esta corrida mide la configuración de producción"; exit 1; }

echo "=== Corrida al pico de diseño (configuración de producción) ==="
echo "Carga: $N atletas × speedup $SPEEDUP ≈ $RATE msg/s | sesión $SESSION"
echo "Escalado: KEDA (sin réplicas fijadas a mano)"
echo "Resultados en: $OUTDIR"
echo ""

# ── muestreo de réplicas: la evidencia del escalado en caliente ─────────────
sample_replicas() {
    local out="$1"
    echo "ts,ready_replicas,keda_metric,cpu_pct" > "$out"
    while true; do
        local ts ready targets metric cpu
        ts="$(date -u +%H:%M:%S)"
        ready="$(kubectl get deployment/processor -n "$NS" -o jsonpath='{.status.readyReplicas}' 2>/dev/null)"
        targets="$(kubectl get hpa -n "$NS" -o jsonpath='{.items[0].status.currentMetrics}' 2>/dev/null)"
        metric="$(printf '%s' "$targets" | python3 -c "
import sys,json
try:
    ms=json.load(sys.stdin)
    v=[m for m in ms if m.get('type')=='External']
    print(v[0]['external']['current']['averageValue'] if v else '')
except Exception: print('')" 2>/dev/null)"
        cpu="$(printf '%s' "$targets" | python3 -c "
import sys,json
try:
    ms=json.load(sys.stdin)
    v=[m for m in ms if m.get('type')=='Resource']
    print(v[0]['resource']['current']['averageUtilization'] if v else '')
except Exception: print('')" 2>/dev/null)"
        echo "$ts,${ready:-0},${metric},${cpu}" >> "$out"
        sleep "$POLL"
    done
}

influx_count() {
    local session="$1"
    local flux="from(bucket:\"$INFLUX_BUCKET\")
      |> range(start: -2d, stop: 1d)
      |> filter(fn:(r) => r._measurement==\"biometrics\" and r.session_id==\"$session\" and r._field==\"heart_rate\")
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

# ── la corrida ──────────────────────────────────────────────────────────────
START_ISO="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

sample_replicas "$OUTDIR/replicas.csv" &
REPL_PID=$!
bash "$ROOT/experiments/load-tests/sample_resources.sh" "$OUTDIR/resources.csv" "$POLL" &
SAMPLER_PID=$!

: > "$OUTDIR/sim.jsonl"
echo "Generando carga (~$(( 4600 / SPEEDUP )) s)..."
"$SIM" --athletes "$N" --athlete-prefix atleta --session-id "$SESSION" \
       --broker-host "$BROKER_HOST" --broker-port "$BROKER_PORT" \
       --broker-password "$DEVICE_PASSWORD" --broker-ca "$CA_FILE" \
       --speedup "$SPEEDUP" --seed 42 --log-level WARNING \
       >> "$OUTDIR/sim.jsonl" 2>"$OUTDIR/sim.log"

# margen para que el último lote se confirme en InfluxDB (flush_interval 0,5 s)
sleep 5
END_ISO="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

# El muestreo de réplicas sigue un poco más: interesa ver el arranque del
# desescalado, que la ventana de estabilización de 180 s retrasa.
kill "$SAMPLER_PID" 2>/dev/null; wait "$SAMPLER_PID" 2>/dev/null

# métricas de cada réplica viva (las que KEDA levantó durante la corrida)
PODS="$(kubectl get pods -n "$NS" -l "$SELECTOR" -o jsonpath='{.items[*].metadata.name}')"
echo "Réplicas al cierre: $PODS"
for pod in $PODS; do
    python3 "$ROOT/experiments/load-tests/collect_metrics.py" \
        --pod "$pod" --namespace "$NS" --start "$START_ISO" --end "$END_ISO" \
        > "$OUTDIR/proc_${pod}.jsonl" 2>>"$OUTDIR/collect.log"
done

kill "$REPL_PID" 2>/dev/null; wait "$REPL_PID" 2>/dev/null

# ── contabilidad ────────────────────────────────────────────────────────────
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
DELIVERED="$(influx_count "$SESSION")"

python3 -c "
import json
json.dump({'run':'peak','athletes':$N,'speedup':$SPEEDUP,'target_rate_msg_s':$RATE,
           'scaling':'keda','session':'$SESSION','start':'$START_ISO','end':'$END_ISO',
           'enqueued':$ENQUEUED,'offered':$OFFERED,'delivered':$DELIVERED},
          open('$OUTDIR/run.json','w'), indent=2)"

echo ""
echo "encolado=$ENQUEUED  ofrecido(acked)=$OFFERED  persistido=$DELIVERED  pérdida=$(( OFFERED - DELIVERED ))"
echo ""
echo "Analiza la corrida con:"
echo "    python3 experiments/load-tests/analyze_peak.py $OUTDIR"
