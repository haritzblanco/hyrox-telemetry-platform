#!/usr/bin/env bash
# Campaña definitiva del capítulo de evaluación, en el orden que hay que seguir.
#
# El 2026-09-10 se aprendió, a base de perder un día, que las cifras del pico solo
# valen si el banco está en un estado conocido. Este guion impone ese estado y
# mide lo importante primero, cuando el banco está más limpio:
#
#   1. Comprueba que el broker esté aislado en el nodo de control. Si comparte
#      nodo con una réplica, el caudal cae de 853 a 571 msg/s y la latencia de
#      transporte pasa de 139 ms a 13 s (results/20260910_192422_broker_coubicado).
#   2. Purga del bucket las sesiones de campañas anteriores, que engordan
#      InfluxDB y frenan la escritura (results/20260910_*_peak_prod_limpio_1).
#   3. Sincroniza relojes y deja asentar.
#   4. Mide las TRES corridas al pico con la configuración de producción, que son
#      las cifras que sostienen el capítulo.
#
# Requisitos: VMs recién arrancadas, nada más corriendo en el anfitrión, y este
# guion como PRIMERA medida de la sesión.
#
# Uso:  bash run_campana_final.sh
set -uo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
NS="${NS:-hyrox}"
REPETICIONES="${REPETICIONES:-3}"
ASENTAR="${ASENTAR:-180}"

paso() { echo ""; echo "=== $1 ==="; }

paso "1/5  Comprobaciones previas"
listos="$(kubectl get nodes --no-headers 2>/dev/null | grep -cw Ready)"
[[ "$listos" -ge 3 ]] || { echo "ERROR: solo $listos nodos Ready; arranca las VMs"; exit 1; }
nodo_broker="$(kubectl get pods -n "$NS" -l app.kubernetes.io/name=mosquitto \
    -o jsonpath='{.items[0].spec.nodeName}' 2>/dev/null)"
echo "  nodos Ready: $listos"
echo "  broker en:   $nodo_broker"
[[ "$nodo_broker" == "k3s-hyrox" ]] || {
    echo "  ERROR: el broker debe estar en el nodo de control. Revisa el nodeSelector"
    echo "         del chart de mosquitto antes de medir."; exit 1; }
uptime | sed 's/.*averages*/  carga del anfitrión:/'

paso "2/5  Purga de las sesiones de campañas anteriores"
CONFIRMAR=si bash "$ROOT/experiments/load-tests/purge_experiment_data.sh" | sed 's/^/  /'

paso "3/5  Relojes"
bash "$ROOT/experiments/load-tests/clock_sync.sh" 2>&1 | grep -E "NTPSync|offset crudo" | sed 's/^/  /'

paso "4/5  Ventana de experimento"
for app in root hyrox-platform; do
    kubectl patch application "$app" -n argocd --type merge \
        -p '{"spec":{"syncPolicy":{"automated":null}}}' >/dev/null 2>&1
done
kubectl apply -f "$ROOT/experiments/load-tests/processor-exp.yaml" >/dev/null
kubectl rollout status deployment/processor -n "$NS" --timeout=180s | sed 's/^/  /'

cerrar_ventana() {
    echo ""
    echo "=== Cerrando la ventana de experimento ==="
    for app in root hyrox-platform; do
        kubectl patch application "$app" -n argocd --type merge \
            -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}' >/dev/null 2>&1
    done
    kubectl get application -n argocd \
        -o custom-columns='NOMBRE:.metadata.name,SYNC:.status.sync.status,SALUD:.status.health.status' \
        --no-headers | sed 's/^/  /'
}
trap cerrar_ventana EXIT

echo "  asentando $ASENTAR s antes de medir"
sleep "$ASENTAR"

paso "5/5  Corridas al pico con la configuración de producción"
for i in $(seq 1 "$REPETICIONES"); do
    OUT="$ROOT/experiments/results/$(date -u +%Y%m%d_%H%M%S)_peak_final_$i"
    echo "  -- repetición $i de $REPETICIONES --"
    N=40 SPEEDUP=20 OUTDIR="$OUT" bash "$ROOT/experiments/load-tests/run_peak.sh" 2>&1 \
        | grep -E "pérdida|ERROR" | sed 's/^/     /'
    python3 "$ROOT/experiments/load-tests/analyze_peak.py" "$OUT" 2>/dev/null \
        | grep -E "transporte|persistencia|extremo a extremo|umbral" | sed 's/^/     /'
    echo "     datos en $(basename "$OUT")"
    sleep 30
done

echo ""
echo "Campaña terminada. Las tres corridas son las cifras del capítulo."
echo "Comprueba en cada una que placement.csv tenga el broker en k3s-hyrox."
