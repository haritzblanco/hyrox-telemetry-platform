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
# Requisitos: VMs arrancadas, relojes sincronizados (clock_sync.sh), Deployment
# del procesador con --metrics-interval activo y metrics-server operativo. La
# ventana de experimento (sincronía de ArgoCD suspendida) debe estar abierta:
# de lo contrario ArgoCD revierte tanto la instrumentación como el escalado
# fijado. Ver experiments/README.md.
#
# Variables (todas con default):
#   REPLICAS="1 2 4"           lista de nº de réplicas del processor
#   N_LIST="4 8 12 16 20"      lista de nº de atletas (carga)
#   SPEEDUP=50                 aceleración temporal → tasa = N × SPEEDUP msg/s
#   BROKER_HOST=192.168.252.2  BROKER_PORT=31883
#   INFLUX_URL=http://192.168.252.2:30086   INFLUX_TOKEN=<del Secret del clúster>
#   SETTLE=6                   segundos de asentamiento tras escalar
#   CPU_LIMIT=""               si se fija (p.ej. 150m), estrangula la CPU de cada
#                              réplica antes de la matriz. Sirve para llevar el
#                              cuello de botella al procesado en vez de al equipo
#                              que genera la carga: con el límite de producción
#                              (1000 m) una sola réplica absorbe el pico entero y
#                              la matriz no distingue una configuración de otra.
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
# El broker exige TLS + autenticación: CA pública y contraseña de dispositivo.
CA_FILE="${CA_FILE:-$ROOT/infra/mqtt/ca.crt}"
DEVICE_PASSWORD="${DEVICE_PASSWORD:-$(kubectl get secret device-broker -n hyrox -o jsonpath='{.data.password}' | base64 -d)}"
INFLUX_ORG="${INFLUX_ORG:-hyrox}"
INFLUX_BUCKET="${INFLUX_BUCKET:-telemetry}"
NS="${NS:-hyrox}"
SELECTOR="app.kubernetes.io/name=processor"
SETTLE="${SETTLE:-6}"
CPU_LIMIT="${CPU_LIMIT:-}"

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
[[ -n "$CPU_LIMIT" ]] && echo "Límite de CPU por réplica: $CPU_LIMIT"
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
    # Con InfluxDB ocupado la consulta puede agotar el plazo. Un 0 en ese caso
    # entraría en el dataset como pérdida total, así que se reintenta y, si no
    # hay respuesta válida, se devuelve vacío para marcar la corrida como no
    # medida en lugar de inventar una cifra.
    local raw
    for _ in 1 2 3; do
        raw="$(curl -s -m 60 --request POST "$INFLUX_URL/api/v2/query?org=$INFLUX_ORG" \
            --header "Authorization: Token $INFLUX_TOKEN" \
            --header "Accept: application/csv" \
            --header "Content-Type: application/vnd.flux" \
            --data "$flux" 2>/dev/null | \
        python3 -c "import sys,csv
tot=None
for r in csv.reader(sys.stdin):
    if r and r[-1].strip().isdigit():
        tot=int(r[-1])
print('' if tot is None else tot)" 2>/dev/null | head -1)"
        if [[ "$raw" =~ ^[0-9]+$ ]]; then
            echo "$raw"
            return 0
        fi
        sleep 5
    done
    echo ""
    return 1
}

# ── estrangular la CPU de cada réplica ──────────────────────────────────────
# La matriz con el límite de producción (1000 m por réplica) mide una plataforma
# que nunca llega a saturarse: una sola réplica cubre el pico de diseño y las
# tres configuraciones dan la misma curva. Bajando el límite se desplaza el
# cuello de botella al procesado, que es la variable que la matriz manipula, y
# el aporte de cada réplica pasa a ser observable. El valor original se restaura
# al terminar para no dejar el clúster estrangulado.
CPU_LIMIT_ORIG=""
set_cpu_limit() {
    local lim="$1"
    CPU_LIMIT_ORIG="$(kubectl get deployment/processor -n "$NS" \
        -o jsonpath='{.spec.template.spec.containers[0].resources.limits.cpu}')"
    echo "### Estrangulando cada réplica a $lim de CPU (antes: ${CPU_LIMIT_ORIG:-sin límite}) ###"
    # requests igual al límite: la réplica queda en calidad de servicio
    # garantizada y el planificador no puede colocar dos donde solo cabe una.
    kubectl patch deployment/processor -n "$NS" --type=json -p "[
        {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/limits/cpu\",\"value\":\"$lim\"},
        {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/requests/cpu\",\"value\":\"$lim\"}]" >/dev/null
    kubectl rollout status deployment/processor -n "$NS" --timeout=180s
}

restore_cpu_limit() {
    [[ -n "$CPU_LIMIT_ORIG" ]] || return 0
    kubectl patch deployment/processor -n "$NS" --type=json -p "[
        {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/limits/cpu\",\"value\":\"$CPU_LIMIT_ORIG\"},
        {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/resources/requests/cpu\",\"value\":\"200m\"}]" >/dev/null 2>&1 || true
}

# ── fijar el número de réplicas ─────────────────────────────────────────────
# Con KEDA desplegado no basta `kubectl scale`: el HPA que gestiona el
# ScaledObject devuelve el Deployment a la cuenta que dicta el disparador en
# cuanto pasa su periodo de sondeo, y la corrida acaba midiendo una topología
# distinta de la que dice el dataset. La anotación `paused-replicas` congela el
# autoescalado en el valor pedido, que es la única forma de garantizar que la
# celda R×N se mide con R réplicas de principio a fin.
pin_replicas() {
    local R="$1"
    if kubectl get scaledobject/processor -n "$NS" >/dev/null 2>&1; then
        kubectl annotate scaledobject/processor -n "$NS" \
            "autoscaling.keda.sh/paused-replicas=$R" --overwrite >/dev/null
    fi
    kubectl scale deployment/processor -n "$NS" --replicas="$R" >/dev/null
    kubectl rollout status deployment/processor -n "$NS" --timeout=120s
}

# Al terminar (o si se interrumpe la tanda) se devuelve el autoescalado a KEDA:
# dejar la anotación puesta congelaría la plataforma en la última celda medida.
unpin_replicas() {
    if kubectl get scaledobject/processor -n "$NS" >/dev/null 2>&1; then
        kubectl annotate scaledobject/processor -n "$NS" \
            autoscaling.keda.sh/paused-replicas- >/dev/null 2>&1 || true
    fi
}
trap 'unpin_replicas; restore_cpu_limit' EXIT

# ── esperar a que la cola del broker se vacíe ───────────────────────────────
# Mientras la plataforma no se satura basta un margen fijo de tres segundos
# para que el último lote llegue a la base de datos. En cuanto una celda satura
# deja de valer: el broker acumula hasta diez mil mensajes por suscriptor y el
# procesado tarda en drenarlos tanto como diga su techo. Contar en ese momento
# apunta como pérdida lo que solo está encolado, y además la celda siguiente
# hereda el atasco de la anterior.
#
# Se espera, por tanto, a que el consumo agregado caiga a cero en dos sondeos
# seguidos. La cifra de pérdida pasa a ser descarte real del broker, y la
# latencia recoge la espera en cola, que es la señal de la saturación.
esperar_drenaje() {
    local limite="${DRAIN_TIMEOUT:-300}" umbral=5 bajos=0 t0=$SECONDS
    while (( SECONDS - t0 < limite )); do
        local suma=0 r
        for pod in $(kubectl get pods -n "$NS" -l "$SELECTOR" \
                     -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
            r="$(kubectl logs "$pod" -n "$NS" --tail=40 2>/dev/null | python3 -c "
import sys,json
ultimo=0
for l in sys.stdin:
    if '\"kind\": \"metrics\"' in l or '\"kind\":\"metrics\"' in l:
        try: ultimo=json.loads(l)['thr_consumed_s']
        except Exception: pass
print(int(ultimo))" 2>/dev/null)"
            suma=$(( suma + ${r:-0} ))
        done
        if (( suma <= umbral )); then
            bajos=$(( bajos + 1 ))
            (( bajos >= 2 )) && break
        else
            bajos=0
        fi
        sleep 12
    done
    DRENAJE_S=$(( SECONDS - t0 ))
}

# ── una corrida (R réplicas, N atletas) ─────────────────────────────────────
run_one() {
    local R="$1" N="$2" suffix="${3:-}"
    local run="R${R}_N${N}${suffix}"
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
           --broker-password "$DEVICE_PASSWORD" --broker-ca "$CA_FILE" \
           --speedup "$SPEEDUP" --seed 42 --log-level WARNING \
           >> "$rundir/sim.jsonl" 2>/dev/null &
    local sim_pid=$!
    wait "$sim_pid" 2>/dev/null || true

    # margen para que el último lote se confirme en InfluxDB (flush_interval 0,5 s)
    sleep 3
    # …y espera a que se vacíe lo que quede encolado en el broker, que con una
    # celda saturada puede ser mucho más que el último lote.
    DRENAJE_S=0
    esperar_drenaje
    (( DRENAJE_S > 15 )) && echo "   drenaje: ${DRENAJE_S}s"
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
    local delivered_json="null" perdida="sin medir"
    if [[ "$delivered" =~ ^[0-9]+$ ]]; then
        delivered_json="$delivered"
        perdida="$(( offered - delivered ))"
    else
        echo "   AVISO: InfluxDB no respondió al conteo; la corrida queda sin pérdida medida"
        delivered="sin medir"
    fi

    python3 -c "
import json
json.dump({'run':'$run','replicas':$R,'athletes':$N,'speedup':$SPEEDUP,
           'target_rate_msg_s':$rate,'session':'$session','cpu_limit':'$CPU_LIMIT' or None,
           'start':'$start_iso','end':'$end_iso',
           'enqueued':$enqueued,'offered':$offered,'delivered':$delivered_json,
           'drain_s':$DRENAJE_S},
          open('$rundir/run.json','w'), indent=2)"
    echo "   encolado=$enqueued  ofrecido(acked)=$offered  persistido=$delivered  (pérdida=$perdida)"
    echo ""
}

[[ -n "$CPU_LIMIT" ]] && set_cpu_limit "$CPU_LIMIT"

# ── matriz o secuencia explícita ────────────────────────────────────────────
# Con SEQUENCE se ejecuta exactamente el orden indicado en lugar del producto
# cartesiano. Sirve para diseños cruzados: repetir la misma celda al principio y
# al final de la tanda separa el efecto de la configuración del efecto del
# desgaste acumulado (InfluxDB compactando, host caliente), que de otro modo se
# confunden porque la matriz recorre las réplicas siempre en el mismo orden.
if [[ -n "${SEQUENCE:-}" ]]; then
    echo "Secuencia explícita: $SEQUENCE (cooldown ${COOLDOWN:-0}s entre corridas)"
    echo ""
    idx=0
    current_r=""
    for pair in $SEQUENCE; do
        R="${pair%%:*}"; N="${pair##*:}"
        idx=$((idx + 1))
        if [[ "$R" != "$current_r" ]]; then
            echo "### Escalando processor a $R réplica(s) ###"
            pin_replicas "$R"
            sleep "$SETTLE"
            current_r="$R"
        fi
        run_one "$R" "$N" "_s${idx}"
        # El descanso deja a InfluxDB terminar de compactar lo de la corrida
        # anterior, para que no lo pague la siguiente.
        if [[ "${COOLDOWN:-0}" -gt 0 ]]; then
            echo "   descanso de ${COOLDOWN}s..."
            sleep "${COOLDOWN}"
        fi
    done
else
    for R in $REPLICAS; do
        echo "### Escalando processor a $R réplica(s) ###"
        pin_replicas "$R"
        sleep "$SETTLE"
        for N in $N_LIST; do
            run_one "$R" "$N"
        done
    done
fi

echo "=== Matriz completada. Analiza con: ==="
echo "    python experiments/notebooks/analyze.py $OUTDIR"
