#!/usr/bin/env bash
# Calibra la ventana de mensajes en vuelo (max_inflight_messages) que hay que
# poner al broker para que el techo de caudal de una réplica caiga dentro del
# rango que el simulador puede ofrecer.
#
# Sustituye al estrangulamiento por cuota de CPU (calibrate_limit.sh), que no
# sirve como instrumento: el techo que produce no es monótono en el límite y
# cambia varias veces entre sesiones (ver results/20260910_155314_calibracion).
# La ventana en vuelo, en cambio, acota el caudal por suscriptor de forma
# determinista, ventana dividida entre tiempo de confirmación, sin depender del
# planificador de CPU. Y al ser por suscriptor, el techo del conjunto crece solo
# con el número de réplicas, que es lo que la matriz necesita medir.
#
# Cada punto reinicia el broker, porque la configuración va montada con subPath
# y no se recoge en caliente, y arranca un pod nuevo del procesador para que la
# sesión anterior se descarte con su cola.
#
# Variables:
#   VENTANAS="5 10 20 40"      valores de max_inflight_messages a probar
#   N=16 SPEEDUP=50            carga de sondeo (800 msg/s por defecto)
set -uo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

VENTANAS="${VENTANAS:-5 10 20 40}"
N="${N:-16}"
SPEEDUP="${SPEEDUP:-50}"
NS="${NS:-hyrox}"
SELECTOR="app.kubernetes.io/name=processor"
BROKER_HOST="${BROKER_HOST:-192.168.252.2}"
BROKER_PORT="${BROKER_PORT:-31883}"
CA_FILE="${CA_FILE:-$ROOT/infra/mqtt/ca.crt}"
DEVICE_PASSWORD="${DEVICE_PASSWORD:-$(kubectl get secret device-broker -n "$NS" -o jsonpath='{.data.password}' | base64 -d)}"
SIM="$ROOT/components/simulator/.venv/bin/hyrox-sim"
CONFIGMAP="$ROOT/infra/helm/hyrox-platform/charts/mosquitto/templates/configmap.yaml"

STAMP="$(date -u +%Y%m%d_%H%M%S)"
OUTDIR="$ROOT/experiments/results/${STAMP}_calibracion_inflight"
mkdir -p "$OUTDIR"

aplicar_ventana() {
    sed "s/^\( *\)max_inflight_messages .*/\1max_inflight_messages $1/" "$CONFIGMAP" \
        | kubectl apply -n "$NS" -f - >/dev/null
    kubectl rollout restart deployment/mosquitto -n "$NS" >/dev/null
    kubectl rollout status deployment/mosquitto -n "$NS" --timeout=180s >/dev/null
}

restaurar() {
    kubectl annotate scaledobject/processor -n "$NS" \
        autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true
    kubectl apply -n "$NS" -f "$CONFIGMAP" >/dev/null 2>&1 || true
    kubectl rollout restart deployment/mosquitto -n "$NS" >/dev/null 2>&1 || true
}
trap restaurar EXIT

kubectl annotate scaledobject/processor -n "$NS" \
    "autoscaling.keda.sh/paused-replicas=1" --overwrite >/dev/null 2>&1 || true

echo "=== Calibración de la ventana de mensajes en vuelo ==="
echo "Carga de sondeo: $N × $SPEEDUP = $(( N * SPEEDUP )) msg/s | ventanas: $VENTANAS"
echo ""
echo "ventana,techo_msg_s,cpu_m,transporte_p95_ms" > "$OUTDIR/calibracion.csv"

for V in $VENTANAS; do
    echo "-- ventana $V --"
    aplicar_ventana "$V"
    kubectl rollout restart deployment/processor -n "$NS" >/dev/null
    kubectl rollout status deployment/processor -n "$NS" --timeout=180s >/dev/null
    sleep 8

    SESSION="inf_${V}_$(date -u +%H%M%S)"
    START_ISO="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
    "$SIM" --athletes "$N" --athlete-prefix atleta --session-id "$SESSION" \
           --broker-host "$BROKER_HOST" --broker-port "$BROKER_PORT" \
           --broker-password "$DEVICE_PASSWORD" --broker-ca "$CA_FILE" \
           --speedup "$SPEEDUP" --seed 42 --log-level WARNING \
           > "$OUTDIR/sim_${V}.jsonl" 2>/dev/null
    END_ISO="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"

    POD="$(kubectl get pods -n "$NS" -l "$SELECTOR" -o jsonpath='{.items[0].metadata.name}')"
    CPU="$(kubectl top pod "$POD" -n "$NS" --no-headers 2>/dev/null | awk '{print $2}')"
    python3 "$ROOT/experiments/load-tests/collect_metrics.py" \
        --pod "$POD" --namespace "$NS" --start "$START_ISO" --end "$END_ISO" \
        > "$OUTDIR/proc_${V}.jsonl" 2>/dev/null

    read -r TECHO LAT <<< "$(python3 -c "
import json,statistics
ws=[json.loads(l) for l in open('$OUTDIR/proc_${V}.jsonl') if l.strip()]
thr=[w['thr_acked_s'] for w in ws if w['thr_acked_s']>0]
lat=[(w.get('lat_transport_ms') or {}).get('p95') for w in ws]
lat=[x for x in lat if x]
carga=[t for t in thr if t >= 0.5*max(thr)] if thr else []
print(round(statistics.median(carga),1) if carga else 0,
      round(statistics.median(lat)) if lat else 0)")"

    echo "$V,$TECHO,$CPU,$LAT" >> "$OUTDIR/calibracion.csv"
    echo "   techo ~ $TECHO msg/s | CPU $CPU | transporte p95 $LAT ms"
    echo ""
done

echo "=== Resultado ==="
column -s, -t < "$OUTDIR/calibracion.csv"
echo ""
echo "Datos en $OUTDIR"
