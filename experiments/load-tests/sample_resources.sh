#!/usr/bin/env bash
# Muestrea el consumo de CPU/memoria de las réplicas del procesador a intervalo
# fijo y lo vuelca a un CSV. Pensado para correr en segundo plano durante una
# corrida y matarlo al terminar (lo hace run_matrix.sh).
#
# Uso:  bash sample_resources.sh <out.csv> [intervalo_s]   (default: 2 s)
#
# Requiere metrics-server (kubectl top). CSV: ts_unix,pod,cpu_m,mem_mi
set -uo pipefail

OUT="${1:?uso: sample_resources.sh <out.csv> [intervalo_s]}"
INTERVAL="${2:-2}"
NS="${NS:-hyrox}"
SELECTOR="${SELECTOR:-app.kubernetes.io/name=processor}"

echo "ts_unix,pod,cpu_m,mem_mi" > "$OUT"

while true; do
    now=$(date +%s)
    # kubectl top: "POD  CPU(cores)  MEMORY(bytes)" → p.ej. "processor-xxx  37m  41Mi"
    kubectl top pods -n "$NS" -l "$SELECTOR" --no-headers 2>/dev/null | \
    while read -r pod cpu mem _; do
        cpu_m=${cpu%m}                      # "37m" → 37
        mem_mi=${mem%Mi}                    # "41Mi" → 41
        echo "${now},${pod},${cpu_m},${mem_mi}" >> "$OUT"
    done
    sleep "$INTERVAL"
done
