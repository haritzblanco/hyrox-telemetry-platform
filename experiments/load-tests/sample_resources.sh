#!/usr/bin/env bash
# Muestrea el consumo de CPU/memoria de las réplicas del procesador a intervalo
# fijo y lo vuelca a un CSV. Pensado para correr en segundo plano durante una
# corrida y matarlo al terminar (lo hace run_matrix.sh).
#
# Uso:  bash sample_resources.sh <out.csv> [intervalo_s]   (default: 2 s)
#
# Requiere metrics-server (kubectl top). CSV: ts_unix,pod,nodo,cpu_m,mem_mi
#
# El nodo va en cada muestra porque es una variable que cambia sola: el
# planificador reparte las réplicas entre los nodos de cómputo en cada reinicio,
# y con quién comparten nodo decide su rendimiento. Sin registrarlo, una tanda de
# corridas idénticas da resultados que alternan sin explicación (ver
# results/20260910_192422_broker_coubicado/validation.md).
set -uo pipefail

OUT="${1:?uso: sample_resources.sh <out.csv> [intervalo_s]}"
INTERVAL="${2:-2}"
NS="${NS:-hyrox}"
SELECTOR="${SELECTOR:-app.kubernetes.io/name=processor}"

echo "ts_unix,pod,nodo,cpu_m,mem_mi" > "$OUT"

while true; do
    now=$(date +%s)
    nodos="$(kubectl get pods -n "$NS" -l "$SELECTOR" \
        -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName' --no-headers 2>/dev/null)"
    # kubectl top: "POD  CPU(cores)  MEMORY(bytes)" → p.ej. "processor-xxx  37m  41Mi"
    kubectl top pods -n "$NS" -l "$SELECTOR" --no-headers 2>/dev/null | \
    while read -r pod cpu mem _; do
        cpu_m=${cpu%m}                      # "37m" → 37
        mem_mi=${mem%Mi}                    # "41Mi" → 41
        nodo=$(printf '%s\n' "$nodos" | awk -v p="$pod" '$1==p {print $2}')
        echo "${now},${pod},${nodo:-?},${cpu_m},${mem_mi}" >> "$OUT"
    done
    sleep "$INTERVAL"
done
