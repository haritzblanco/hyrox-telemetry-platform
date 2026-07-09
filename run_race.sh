#!/usr/bin/env bash
# Lanza el procesador y N atletas simultáneos contra el stack local (k3s).
#
# Uso:
#   ./run_race.sh [N_ATLETAS]        # una carrera (default: 5 atletas)
#   LOOP=true ./run_race.sh [N]      # loop continuo: nueva sesión al terminar
#   SPEEDUP=60 ./run_race.sh 8       # 8 atletas, 1 min real = 1h de carrera
#   LOCAL_PROCESSOR=false ./run_race.sh 8  # solo simuladores; procesa el clúster
#
# Variables de entorno (todas opcionales, tienen defaults):
#   N_ATLETAS, SPEEDUP, LOOP, BROKER_HOST, BROKER_PORT, INFLUX_URL, INFLUX_TOKEN
#   LOCAL_PROCESSOR  true (def): arranca un procesador local. false: consume
#                    el procesador desplegado en el clúster.

set -euo pipefail

# Configuración
N_ATLETAS=${1:-${N_ATLETAS:-20}}
# Si no se fija, el speedup se ajusta para que la tasa (atletas x speedup)
# ronde los 600 msg/s: por encima de ~700/s el consumidor satura y mosquitto
# empieza a descartar.
SPEEDUP=${SPEEDUP:-$(python3 -c "print(min(60, max(8, 600 // max(1, $N_ATLETAS))))")}
LOOP=${LOOP:-false}
LOCAL_PROCESSOR=${LOCAL_PROCESSOR:-true}
BROKER_HOST=${BROKER_HOST:-192.168.252.2}
BROKER_PORT=${BROKER_PORT:-31883}
INFLUX_URL=${INFLUX_URL:-http://192.168.252.2:30086}
INFLUX_TOKEN=${INFLUX_TOKEN:-token1234567890}

SIM="components/simulator/.venv/bin/hyrox-sim"
PROC="components/processor/.venv/bin/hyrox-processor"

FITNESS_MEAN=${FITNESS_MEAN:-1.0}
FITNESS_SD=${FITNESS_SD:-0.15}
FITNESS_SEED=${FITNESS_SEED:-7}
# Cuatro parámetros por atleta, sorteados con semilla fija (misma orden =
# mismos atletas). Rangos calibrados con las 3 carreras reales:
#   fitness   normal truncada [0.84, 1.60]: de ~56 min (el récord ronda 54) a ~1h45
#   tilt      gauss(0, 0.06) truncado a 0.14; run_factor=1+tilt, strength_factor=1-tilt
#   hr_offset gauss(0, 7) bpm, truncado a 15
#   drift     uniforme 12-34 bpm
# Salida: 5 valores por atleta, aplanados en un array (bash 3.2, sin mapfile).
PERSONAS=( $(python3 -c "
import random
random.seed($FITNESS_SEED)
for _ in range($N_ATLETAS):
    fit=min(1.60,max(0.84,random.gauss($FITNESS_MEAN,$FITNESS_SD)))
    tilt=max(-0.14,min(0.14,random.gauss(0,0.06)))
    hro=min(15.0,max(-15.0,random.gauss(0,7)))
    dr =random.uniform(12,34)
    print('%.3f %.3f %.3f %.1f %.1f'%(fit,1.0+tilt,1.0-tilt,hro,dr))
") )

# Comprobaciones previas
cd "$(dirname "$0")"

[[ -x "$SIM" ]]  || { echo "ERROR: no encontrado $SIM (¿instalaste el venv?)"; exit 1; }
if [[ "$LOCAL_PROCESSOR" == "true" ]]; then
    [[ -x "$PROC" ]] || { echo "ERROR: no encontrado $PROC (¿instalaste el venv?)"; exit 1; }
fi

# Gestión de procesos
ALL_PIDS=()

cleanup() {
    echo ""
    echo "Deteniendo todos los procesos..."
    for pid in "${ALL_PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    exit 0
}
trap cleanup SIGINT SIGTERM

# Procesador
PROC_PID=""
if [[ "$LOCAL_PROCESSOR" == "true" ]]; then
    echo "Iniciando procesador local (MQTT a InfluxDB)..."
    "$PROC" \
        --broker-host "$BROKER_HOST" --broker-port "$BROKER_PORT" \
        --influx-url "$INFLUX_URL" --influx-token "$INFLUX_TOKEN" \
        --log-level WARNING &
    PROC_PID=$!
    ALL_PIDS+=("$PROC_PID")
    sleep 0.5
else
    echo "LOCAL_PROCESSOR=false: no se arranca procesador local; consume el del clúster."
fi

# Una carrera completa
run_race() {
    local session_id="$1"
    local athlete_pids=()

    echo ""
    echo "=== Sesión $session_id | $N_ATLETAS atletas | speedup ${SPEEDUP}x ==="

    for i in $(seq 1 "$N_ATLETAS"); do
        local id
        id=$(printf "atleta-%03d" "$i")
        # 5 valores por atleta aplanados en PERSONAS
        local b=$(( (i-1) * 5 ))
        local fitness="${PERSONAS[$b]:-1.0}"
        local run_factor="${PERSONAS[$((b+1))]:-1.0}"
        local strength_factor="${PERSONAS[$((b+2))]:-1.0}"
        local hr_offset="${PERSONAS[$((b+3))]:-0}"
        local drift="${PERSONAS[$((b+4))]:-25}"

        "$SIM" \
            --athlete-id      "$id" \
            --session-id      "$session_id" \
            --broker-host     "$BROKER_HOST" \
            --broker-port     "$BROKER_PORT" \
            --speedup         "$SPEEDUP" \
            --fitness         "$fitness" \
            --run-factor      "$run_factor" \
            --strength-factor "$strength_factor" \
            --hr-offset       "$hr_offset" \
            --drift           "$drift" \
            --seed            "$((i * 42))" \
            --log-level       WARNING &

        athlete_pids+=($!)
        ALL_PIDS+=($!)
        printf "  %-12s  fit=%s run=%s str=%s hr_offset=%s drift=%s\n" \
            "$id" "$fitness" "$run_factor" "$strength_factor" "$hr_offset" "$drift"
    done

    echo ""
    echo "Esperando a que terminen los atletas..."
    for pid in "${athlete_pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
    echo "Carrera completada."
}

# Bucle principal
echo ""
echo "=== HYROX Race Simulator ==="
echo "Broker: $BROKER_HOST:$BROKER_PORT | InfluxDB: $INFLUX_URL"
echo ""

if [[ "$LOOP" == "true" ]]; then
    race=1
    while true; do
        session_id=$(date -u +"%Y%m%d_%H%M%S")
        echo "Carrera #$race"
        run_race "$session_id"
        ((race++))
        sleep 1
    done
else
    session_id=$(date -u +"%Y%m%d_%H%M%S")
    run_race "$session_id"
fi

[[ -n "$PROC_PID" ]] && kill "$PROC_PID" 2>/dev/null || true
