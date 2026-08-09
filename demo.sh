#!/usr/bin/env bash
# Helper para la demo ante las directoras. Encapsula los pasos del guion (ver
# DEMO.md) con LOCAL_PROCESSOR=false ya fijado: SIEMPRE procesa el clúster, nunca
# un procesador local (evita la doble escritura).
#
# Uso:
#   ./demo.sh check          # comprobaciones previas (reloj, pods, Grafana)
#   ./demo.sh seed [N]        # carrera COMPLETA pre-cargada (rápida, speedup 60)
#   ./demo.sh live [N]        # carrera EN DIRECTO (speedup 8, evoluciona ~8 min)
#   ./demo.sh gitops          # el lazo GitOps en vivo: deriva manual → ArgoCD la revierte
#   ./demo.sh hpa             # carga sostenida ~1200 msg/s → el HPA escala 1→4 solo
#   ./demo.sh hpa stop        # corta la carga (el HPA desescala solo en ~5 min)
#   ./demo.sh carga [N]       # prueba de carga con contabilidad: ofrecido vs persistido
#   ./demo.sh scale [N]       # escala el procesador a N réplicas
#   ./demo.sh heal            # borra un pod del procesador (auto-recuperación)
#   ./demo.sh clean           # borra todas las lecturas de InfluxDB (con confirmación)
#   ./demo.sh pods            # estado de los pods
#   ./demo.sh logs            # logs de las réplicas del procesador
#   ./demo.sh urls            # imprime las URLs de los 3 dashboards

set -euo pipefail
cd "$(dirname "$0")"

NS=hyrox
NODE=192.168.252.2
GRAFANA="http://$NODE:30300"
# Credenciales desde los Secret del clúster; ya no van en el repo.
GRAFANA_LOGIN="admin / $(kubectl get secret grafana-auth -n $NS -o jsonpath='{.data.admin-password}' | base64 -d)"
INFLUX_URL=${INFLUX_URL:-http://$NODE:30086}
INFLUX_TOKEN=${INFLUX_TOKEN:-$(kubectl get secret influxdb-auth -n $NS -o jsonpath='{.data.token}' | base64 -d)}

green() { printf "\033[32m%s\033[0m\n" "$1"; }
red()   { printf "\033[31m%s\033[0m\n" "$1"; }
bold()  { printf "\033[1m%s\033[0m\n" "$1"; }

cmd_check() {
    bold "1) Resincronizando relojes de las 3 VMs..."
    for vm in k3s-hyrox hyrox-worker hyrox-worker2; do
        multipass exec "$vm" -- sudo systemctl restart systemd-timesyncd || true
    done
    sleep 2
    echo "   Mac : $(date -u)"
    echo "   k3s : $(multipass exec k3s-hyrox -- date -u)"

    bold "2) Estado de los pods (todos deben estar Running):"
    kubectl get pods -n "$NS" -o wide

    bold "3) Grafana:"
    code=$(curl -s -m 5 -o /dev/null -w "%{http_code}" "$GRAFANA/api/health" || echo "000")
    if [[ "$code" == "200" ]]; then green "   Grafana OK (HTTP 200)"; else red "   Grafana NO responde (HTTP $code)"; fi

    bold "4) Réplicas del procesador y su suscripción:"
    kubectl get deployment/processor -n "$NS" -o jsonpath='   réplicas deseadas: {.spec.replicas}{"\n"}'
}

cmd_seed() {
    local n="${1:-15}"
    bold "Pre-cargando una carrera COMPLETA de $n atletas (speedup 60, ~1 min)..."
    LOCAL_PROCESSOR=false SPEEDUP=60 ./run_race.sh "$n"
    green "Carrera pre-cargada. Úsala en los dashboards de Clasificación y Análisis."
}

cmd_live() {
    local n="${1:-12}"
    bold "Lanzando carrera EN DIRECTO de $n atletas (speedup 8, dura ~8 min)."
    echo "Abre el dashboard Live y déjala correr mientras hablas. Ctrl-C para cortar."
    LOCAL_PROCESSOR=false SPEEDUP=8 ./run_race.sh "$n"
}

SIM="components/simulator/.venv/bin/hyrox-sim"
HPA_PID_FILE="/tmp/demo_hpa_load.pid"
# El broker exige TLS + autenticación: CA pública para validar el servidor y la
# contraseña de dispositivo desde el Secret del clúster. Cada atleta usa su id.
CA_FILE=${CA_FILE:-infra/mqtt/ca.crt}
DEVICE_PASSWORD=${DEVICE_PASSWORD:-$(kubectl get secret device-broker -n $NS -o jsonpath='{.data.password}' | base64 -d)}

cmd_gitops() {
    bold "1) La plataforma según ArgoCD (todo lo que hay desplegado sale del repo):"
    kubectl get application hyrox-platform -n argocd \
        -o jsonpath='   repo: {.spec.source.repoURL}{"\n"}   estado: {.status.sync.status} / {.status.health.status}{"\n"}   revisión: {.status.sync.revision}{"\n"}'
    echo
    bold "2) Provocamos una deriva: cambiamos la imagen del procesador A MANO..."
    local original
    original=$(kubectl get deployment processor -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].image}')
    kubectl set image deployment/processor -n "$NS" processor=hyrox/processor:0.1.0
    echo "   imagen ahora: $(kubectl get deployment processor -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].image}')  (declarada en el repo: $original)"
    echo
    bold "3) Esperando a que ArgoCD detecte la deriva y la revierta solo..."
    local t0; t0=$(date +%s)
    until [[ "$(kubectl get deployment processor -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].image}')" == "$original" ]]; do
        sleep 5; printf "."
    done
    echo
    green "   Revertido en $(( $(date +%s) - t0 )) s: la imagen vuelve a ser $original."
    echo "   Nadie ha ejecutado nada: el lazo de reconciliación ha restaurado lo declarado."
}

cmd_hpa() {
    if [[ "${1:-}" == "stop" ]]; then
        if [[ -f "$HPA_PID_FILE" ]]; then
            kill "$(cat "$HPA_PID_FILE")" 2>/dev/null || true
            rm -f "$HPA_PID_FILE"
            green "Carga cortada. El HPA desescalará 4→1 él solo en ~5 min (ventana de estabilización)."
        else
            red "No hay carga en marcha."
        fi
        return 0
    fi
    bold "Lanzando carga sostenida: 20 atletas × speedup 60 ≈ 1.200 msg/s (equivale a 1.200 atletas en pista)."
    nohup "$SIM" --athletes 20 --speedup 60 --loop \
        --broker-host "$NODE" --broker-port 31883 --session-id demo_hpa \
        --broker-password "$DEVICE_PASSWORD" --broker-ca "$CA_FILE" \
        --log-level ERROR >/dev/null 2>&1 &
    echo $! > "$HPA_PID_FILE"
    echo "Observa el HPA reaccionar (Ctrl-C para dejar de mirar; la carga sigue hasta './demo.sh hpa stop'):"
    echo "  esperado: 1→2 réplicas en ~1 min, 2→4 en ~1,5 min, régimen al ~75% de CPU"
    kubectl get hpa processor -n "$NS" -w
}

cmd_carga() {
    local n="${1:-16}"
    local rate=$(( n * 50 ))
    local session; session="demo_carga_$(date +%H%M%S)"
    bold "Prueba de carga: $n atletas × speedup 50 ≈ $rate msg/s durante ~90 s (sesión $session)."
    echo "El simulador cuenta lo que el broker CONFIRMA (PUBACK); al final se compara con lo persistido."
    echo
    local out; out=$("$SIM" --athletes "$n" --speedup 50 --session-id "$session" \
        --broker-host "$NODE" --broker-port 31883 \
        --broker-password "$DEVICE_PASSWORD" --broker-ca "$CA_FILE" \
        --log-level ERROR 2>/dev/null)
    sleep 3   # margen para que el último lote se confirme en InfluxDB
    local offered; offered=$(echo "$out" | python3 -c "
import sys, json
print(sum(json.loads(l)['acked'] for l in sys.stdin if l.strip()))")
    local delivered; delivered=$(curl -s -m 30 --request POST "$INFLUX_URL/api/v2/query?org=hyrox" \
        --header "Authorization: Token $INFLUX_TOKEN" --header "Accept: application/csv" \
        --header "Content-Type: application/vnd.flux" \
        --data "from(bucket:\"telemetry\") |> range(start:-15m)
          |> filter(fn:(r)=>r._measurement==\"biometrics\" and r.session_id==\"$session\" and r._field==\"heart_rate\")
          |> group() |> count()" | tail -2 | head -1 | awk -F, '{print $NF}' | tr -d '\r')
    delivered=${delivered:-0}
    echo
    bold "Resultado:"
    echo "   ofrecido (confirmado por el broker) : $offered lecturas"
    echo "   persistido (contado en InfluxDB)    : $delivered lecturas"
    python3 -c "
o, d = $offered, $delivered
loss = (o - d) / o * 100 if o else 0
print(f'   pérdida                             : {o - d} lecturas ({loss:.2f}%)')"
    echo
    echo "Las campañas completas usan este mismo método sobre una matriz réplicas × carga"
    echo "(experiments/load-tests/run_matrix.sh); resultados en experiments/results/."
}

cmd_scale() {
    local n="${1:-4}"
    bold "Escalando el procesador a $n réplicas..."
    kubectl scale deployment/processor -n "$NS" --replicas="$n"
    kubectl rollout status deployment/processor -n "$NS" --timeout=90s
    kubectl get pods -n "$NS" -l app.kubernetes.io/name=processor -o wide
    green "Mira los logs (./demo.sh logs): todas se suscriben a \$share/processors/..."
}

cmd_heal() {
    local pod
    pod=$(kubectl get pods -n "$NS" -l app.kubernetes.io/name=processor \
          -o jsonpath='{.items[0].metadata.name}')
    bold "Borrando el pod $pod — Kubernetes debe recrearlo solo..."
    kubectl delete pod "$pod" -n "$NS"
    echo "Observa la recuperación (Ctrl-C para salir):"
    kubectl get pods -n "$NS" -l app.kubernetes.io/name=processor -w
}

cmd_clean() {
    bold "Esto BORRARÁ todas las lecturas (measurement 'biometrics') de InfluxDB."
    echo "Todos los datos de la base son simulados; la calibración del simulador vive"
    echo "en hyrox_profile.json, NO en InfluxDB. No se pierde nada irrecuperable:"
    echo "vuelve a poblarse con './demo.sh seed'."
    printf "Para confirmar, escribe 'si': "
    read -r ans
    [[ "$ans" == "si" ]] || { red "Cancelado."; return 0; }
    bold "Borrando..."
    curl -s -XPOST "$INFLUX_URL/api/v2/delete?org=hyrox&bucket=telemetry" \
        -H "Authorization: Token $INFLUX_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"start":"1970-01-01T00:00:00Z","stop":"2100-01-01T00:00:00Z","predicate":"_measurement=\"biometrics\""}' \
        -w "  HTTP %{http_code}\n"
    green "InfluxDB limpio. Lanza './demo.sh seed' para una carrera nueva."
}

cmd_pods() { kubectl get pods -n "$NS" -o wide; }

cmd_logs() {
    for p in $(kubectl get pods -n "$NS" -l app.kubernetes.io/name=processor -o name); do
        bold "## $p"
        kubectl logs -n "$NS" "$p" --tail=6
    done
}

cmd_urls() {
    bold "Grafana ($GRAFANA_LOGIN):"
    echo "  Live          : $GRAFANA/d/hyrox-live"
    echo "  Clasificación : $GRAFANA/d/hyrox-clasificacion"
    echo "  Análisis      : $GRAFANA/d/hyrox-analysis"
    echo "  Comparativa   : $GRAFANA/d/hyrox-compare   (bonus)"
}

case "${1:-}" in
    check)  cmd_check ;;
    seed)   cmd_seed "${2:-}" ;;
    live)   cmd_live "${2:-}" ;;
    gitops) cmd_gitops ;;
    hpa)    cmd_hpa "${2:-}" ;;
    carga)  cmd_carga "${2:-}" ;;
    scale)  cmd_scale "${2:-}" ;;
    heal)   cmd_heal ;;
    clean)  cmd_clean ;;
    pods)   cmd_pods ;;
    logs)   cmd_logs ;;
    urls)   cmd_urls ;;
    *) echo "Uso: ./demo.sh {check|seed [N]|live [N]|gitops|hpa [stop]|carga [N]|scale [N]|heal|clean|pods|logs|urls}"; exit 1 ;;
esac
