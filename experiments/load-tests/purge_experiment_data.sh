#!/usr/bin/env bash
# Borra del bucket las sesiones escritas por el arnés, dejando la de la demo.
#
# Los experimentos escriben en el mismo bucket que la demo, así que cada campaña
# contamina a la siguiente: el 2026-09-10 el bucket pasó a 7,6 millones de puntos
# en una jornada y la latencia de persistencia subió de 530 a 810 ms, arrastrando
# consigo al transporte. Conviene purgar antes de una campaña que vaya a dar
# cifras para la memoria.
#
# Las sesiones del arnés se reconocen por prefijo: exp_, cal_, inf_, peak_. Se
# respetan las de la demo y las figuras (demo_cap10, paneles_cap5); quedan fuera
# también unas pocas de diagnóstico con nombres sueltos (diag_, failover_, test),
# que son pequeñas. Los prefijos se pueden cambiar con PREFIJOS.
#
# Uso:  CONFIRMAR=si bash purge_experiment_data.sh
# Sin CONFIRMAR solo enumera lo que borraría.
set -uo pipefail

cd "$(dirname "$0")/../.."
NS="${NS:-hyrox}"
INFLUX_URL="${INFLUX_URL:-http://192.168.252.2:30086}"
INFLUX_ORG="${INFLUX_ORG:-hyrox}"
INFLUX_BUCKET="${INFLUX_BUCKET:-telemetry}"
INFLUX_TOKEN="${INFLUX_TOKEN:-$(kubectl get secret influxdb-auth -n "$NS" -o jsonpath='{.data.token}' | base64 -d)}"
PREFIJOS="${PREFIJOS:-exp_ cal_ inf_ peak_}"

sesiones="$(curl -s -XPOST "$INFLUX_URL/api/v2/query?org=$INFLUX_ORG" \
    -H "Authorization: Token $INFLUX_TOKEN" \
    -H 'Content-Type: application/vnd.flux' -H 'Accept: application/csv' \
    -d "import \"influxdata/influxdb/schema\"
schema.tagValues(bucket: \"$INFLUX_BUCKET\", tag: \"session_id\")" \
    | awk -F, 'NR>1 && NF>3 {gsub(/\r/,"",$4); if ($4 != "") print $4}')"

a_borrar=""
for s in $sesiones; do
    for p in $PREFIJOS; do
        case "$s" in "$p"*) a_borrar="$a_borrar $s"; break ;; esac
    done
done

if [[ -z "${a_borrar// /}" ]]; then
    echo "No hay sesiones de experimento en el bucket."
    exit 0
fi

echo "Sesiones de experimento en $INFLUX_BUCKET:"
for s in $a_borrar; do echo "  $s"; done
echo ""

if [[ "${CONFIRMAR:-}" != "si" ]]; then
    echo "Simulacro. Para borrarlas de verdad: CONFIRMAR=si bash $0"
    exit 0
fi

for s in $a_borrar; do
    curl -s -XPOST "$INFLUX_URL/api/v2/delete?org=$INFLUX_ORG&bucket=$INFLUX_BUCKET" \
        -H "Authorization: Token $INFLUX_TOKEN" -H 'Content-Type: application/json' \
        -d "{\"start\":\"2020-01-01T00:00:00Z\",\"stop\":\"2100-01-01T00:00:00Z\",
             \"predicate\":\"session_id=\\\"$s\\\"\"}" >/dev/null
    echo "  borrada $s"
done
echo ""
echo "Purga terminada."
