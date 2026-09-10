#!/usr/bin/env bash
# Calibra el límite de CPU que hay que poner a una réplica para que su techo
# de caudal caiga dentro del rango que el simulador puede ofrecer sin saturar
# el equipo generador.
#
# El consumo medio que reporta `kubectl top` no sirve para predecirlo: una
# réplica que a 800 msg/s promedia 293 m no absorbe 800 msg/s con un límite de
# 300 m. El límite es una cuota por periodo, no un promedio, y cada vez que el
# proceso la agota queda detenido hasta el periodo siguiente, lo que rompe el
# agrupamiento de escrituras y encarece cada mensaje. La relación entre límite
# y techo hay que medirla.
#
# Cada punto arranca con un pod nuevo, de modo que la sesión MQTT anterior se
# descarte con su cola y el punto no herede el atasco del anterior.
#
# Variables:
#   LIMITES="300m 500m"        límites a probar, en orden
#   N=16 SPEEDUP=50            carga de sondeo (por defecto 800 msg/s, muy por
#                              encima del techo esperado: interesa la meseta)
set -uo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

LIMITES="${LIMITES:-300m 500m}"
N="${N:-16}"
SPEEDUP="${SPEEDUP:-50}"
NS="${NS:-hyrox}"
SELECTOR="app.kubernetes.io/name=processor"
BROKER_HOST="${BROKER_HOST:-192.168.252.2}"
BROKER_PORT="${BROKER_PORT:-31883}"
CA_FILE="${CA_FILE:-$ROOT/infra/mqtt/ca.crt}"
DEVICE_PASSWORD="${DEVICE_PASSWORD:-$(kubectl get secret device-broker -n "$NS" -o jsonpath='{.data.password}' | base64 -d)}"
SIM="$ROOT/components/simulator/.venv/bin/hyrox-sim"

STAMP="$(date -u +%Y%m%d_%H%M%S)"
OUTDIR="$ROOT/experiments/results/${STAMP}_calibracion"
mkdir -p "$OUTDIR"

LIMITE_ORIG="$(kubectl get deployment/processor -n "$NS" \
    -o jsonpath='{.spec.template.spec.containers[0].resources.limits.cpu}')"

restaurar() {
    kubectl annotate scaledobject/processor -n "$NS" \
        autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true
    kubectl patch deployment/processor -n "$NS" --type=json -p "[
        {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/limits/cpu\",\"value\":\"$LIMITE_ORIG\"},
        {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/requests/cpu\",\"value\":\"200m\"}]" >/dev/null 2>&1 || true
}
trap restaurar EXIT

kubectl annotate scaledobject/processor -n "$NS" \
    "autoscaling.keda.sh/paused-replicas=1" --overwrite >/dev/null 2>&1 || true

echo "=== Calibración del límite de CPU ==="
echo "Carga de sondeo: $N × $SPEEDUP = $(( N * SPEEDUP )) msg/s | límites: $LIMITES"
echo ""
echo "limite,techo_msg_s,cpu_m,transporte_p95_ms,nodo,nodo_broker" > "$OUTDIR/calibracion.csv"

for LIM in $LIMITES; do
    I=$((I+1))
    echo "── límite $LIM (punto $I) ──────────────────────────────"
    kubectl patch deployment/processor -n "$NS" --type=json -p "[
        {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/limits/cpu\",\"value\":\"$LIM\"},
        {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/requests/cpu\",\"value\":\"$LIM\"}]" >/dev/null
    kubectl rollout status deployment/processor -n "$NS" --timeout=180s
    sleep 8

    SESSION="cal_${LIM}_$(date -u +%H%M%S)"
    START_ISO="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
    "$SIM" --athletes "$N" --athlete-prefix atleta --session-id "$SESSION" \
           --broker-host "$BROKER_HOST" --broker-port "$BROKER_PORT" \
           --broker-password "$DEVICE_PASSWORD" --broker-ca "$CA_FILE" \
           --speedup "$SPEEDUP" --seed 42 --log-level WARNING \
           > "$OUTDIR/sim_${I}_${LIM}.jsonl" 2>/dev/null
    END_ISO="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

    POD="$(kubectl get pods -n "$NS" -l "$SELECTOR" -o jsonpath='{.items[0].metadata.name}')"
    CPU="$(kubectl top pod "$POD" -n "$NS" --no-headers 2>/dev/null | awk '{print $2}')"
    # El nodo de la réplica y el del broker: si comparten, el techo se desploma.
    NODO="$(kubectl get pod "$POD" -n "$NS" -o jsonpath='{.spec.nodeName}' 2>/dev/null)"
    NODO_BROKER="$(kubectl get pods -n "$NS" -l app.kubernetes.io/name=mosquitto \
        -o jsonpath='{.items[0].spec.nodeName}' 2>/dev/null)"
    python3 "$ROOT/experiments/load-tests/collect_metrics.py" \
        --pod "$POD" --namespace "$NS" --start "$START_ISO" --end "$END_ISO" \
        > "$OUTDIR/proc_${I}_${LIM}.jsonl" 2>/dev/null

    # El techo es la mediana de las ventanas en carga, no la media de la
    # corrida: las primeras ventanas van por debajo mientras el proceso
    # arranca y arrastrarían la cifra hacia abajo.
    read -r TECHO LAT <<< "$(python3 -c "
import json,statistics
ws=[json.loads(l) for l in open('$OUTDIR/proc_${I}_${LIM}.jsonl') if l.strip()]
thr=[w['thr_acked_s'] for w in ws if w['thr_acked_s']>0]
lat=[(w.get('lat_transport_ms') or {}).get('p95') for w in ws]
lat=[x for x in lat if x]
carga=[t for t in thr if t >= 0.5*max(thr)] if thr else []
print(round(statistics.median(carga),1) if carga else 0,
      round(statistics.median(lat)) if lat else 0)")"

    echo "$LIM,$TECHO,$CPU,$LAT,$NODO,$NODO_BROKER" >> "$OUTDIR/calibracion.csv"
    echo "   techo ≈ $TECHO msg/s | CPU $CPU | transporte p95 $LAT ms"
    [[ "$NODO" == "$NODO_BROKER" ]] && echo "   AVISO: la replica comparte nodo con el broker; el techo no es comparable"
    echo ""
done

echo "=== Resultado ==="
column -s, -t < "$OUTDIR/calibracion.csv"
echo ""
echo "Datos en $OUTDIR"
