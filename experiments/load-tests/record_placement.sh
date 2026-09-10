#!/usr/bin/env bash
# Deja escrito en qué nodo corre cada pod de la plataforma al empezar una corrida.
#
# No es un adorno: el reparto cambia solo. El planificador mueve los pods en cada
# reinicio, y con quién comparte nodo una réplica del procesador decide su
# rendimiento. El 2026-09-10 el broker no estaba fijado a ningún nodo y caía a
# veces sobre un nodo de cómputo; ahí una réplica pasaba de 853 msg/s con 139 ms
# de transporte a 571 msg/s con 13 s, y una tanda de corridas idénticas alternaba
# entre los dos estados sin que nada en los datos lo explicara. Costó un día
# encontrarlo porque el reparto no se guardaba.
#
# Uso:  bash record_placement.sh <out.csv>
set -uo pipefail

OUT="${1:?uso: record_placement.sh <out.csv>}"
NS="${NS:-hyrox}"

echo "pod,nodo" > "$OUT"
kubectl get pods -n "$NS" --field-selector=status.phase=Running \
    -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName' --no-headers 2>/dev/null \
    | awk '{print $1","$2}' >> "$OUT"
