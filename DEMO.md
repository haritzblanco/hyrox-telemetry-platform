# Guion de demo — avances desde junio (comandos en directo)

> Idea central: en junio enseñamos la plataforma funcionando; hoy la enseñamos
> **medida, operada y mejorada con datos**. Tres números nuevos: GitOps
> revirtiendo deriva en segundos, el autoescalador siguiendo a los atletas, y
> las pruebas de carga con su contabilidad.

Formato: dos terminales en la raíz del repo. **T1** para watches (se queda en
pantalla), **T2** para actuar. Al empezar, en ambos:

```bash
cd ~/Desktop/hyrox-telemetry-platform
export SIM=components/simulator/.venv/bin/hyrox-sim
export BROKER=192.168.252.2
# Credenciales desde el clúster (ya no van en el repo)
export INFLUX_TOKEN=$(kubectl get secret influxdb-auth -n hyrox -o jsonpath='{.data.token}' | base64 -d)
export GRAFANA_PASS=$(kubectl get secret grafana-auth -n hyrox -o jsonpath='{.data.admin-password}' | base64 -d)
```

## Antes de la reunión (10 min)

```bash
# Relojes (worker2 se desfasa tras suspensiones y NTPSynchronized miente)
for vm in k3s-hyrox hyrox-worker hyrox-worker2; do
  multipass exec $vm -- sudo systemctl restart systemd-timesyncd
done

# Todo Running, HPA en 1 réplica (si está en 4, espera ~6 min a que baje)
kubectl get pods -n hyrox -o wide
kubectl get hpa -n hyrox

# Carrera pre-cargada para los dashboards de Clasificación y Análisis (~70 s)
$SIM --athletes 15 --speedup 60 --session-id demo_seed \
     --broker-host $BROKER --broker-port 31883 --log-level ERROR
```

Pestañas de Grafana abiertas (login `admin` con `echo $GRAFANA_PASS`):
`http://192.168.252.2:30300/d/hyrox-live` · `/d/hyrox-clasificacion` · `/d/hyrox-analysis`

---

## 1. Arquitectura: qué hay corriendo (2 min)

```bash
kubectl get nodes
kubectl get pods -n hyrox -o wide
```

> "Tres nodos: el plano de control con el broker, la base de datos y Grafana,
> y dos nodos de cómputo para el procesador, que es lo único que escala. Cada
> componente es un contenedor y el dato cruza la red real entre máquinas."

## 2. GitOps: el clúster es el repositorio (4 min)

```bash
kubectl get application hyrox-platform -n argocd
```

> "Todo lo desplegado sale de este repositorio público; yo no ejecuto
> despliegues. Y ahora voy a romper el clúster a propósito."

En **T1**, deja mirando la imagen del procesador:

```bash
while true; do kubectl get deployment processor -n hyrox \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'; sleep 2; done
```

En **T2**, la deriva:

```bash
kubectl -n hyrox set image deployment/processor processor=hyrox/processor:0.1.0
```

En T1 se ve `0.1.0`... y a los pocos segundos vuelve `0.5.0` **solo** (medido:
6 s). Corta el bucle de T1 con Ctrl-C.

> "ArgoCD ha detectado que la realidad no coincide con lo declarado y la ha
> revertido. Nadie ha ejecutado nada. El clúster es siempre lo que dice el
> repositorio."

## 3. Elasticidad: el HPA sigue a los atletas (6 min)

En **T1**, el watch del autoescalador:

```bash
kubectl get hpa processor -n hyrox -w
```

En **T2**, 1.200 atletas en pista:

```bash
$SIM --athletes 20 --speedup 60 --loop --session-id demo_hpa \
     --broker-host $BROKER --broker-port 31883 --log-level ERROR &
```

En T1: el TARGET pasa de ~3% a >150%, y REPLICAS sube 1→2 (~1 min) →4 (~1,5
min). Cuando haya subido, enseña el reparto:

```bash
kubectl get pods -n hyrox -l app.kubernetes.io/name=processor -o wide
```

> "Una réplica declara 200 milicores de capacidad nominal, un número que sale
> de la evaluación experimental, no del aire. Al pasar del 75% el autoescalador
> añade réplicas, repartidas entre los dos workers por anti-afinidad. Cuando la
> pista se vacíe las devolverá solo, eso tarda unos 5 minutos y lo vemos al final."

Corta la carga (la vuelta 4→1 se enseña al cierre):

```bash
kill %1
```

## 4. Carrera en directo + dashboards (5 min)

```bash
$SIM --athletes 12 --speedup 8 --session-id demo_live \
     --broker-host $BROKER --broker-port 31883 --log-level ERROR &
```

Dashboard **Live** (evoluciona ~8 min de fondo mientras hablas): atletas por
fase, series de pulso/potencia/velocidad, y los dos contadores de arriba.

> "Cada atleta emite una lectura por segundo. Este panel de latencia extremo a
> extremo ya no es una promesa: la evaluación lo midió, el dato fresco está
> típicamente en torno al décimo de segundo."

Luego **Clasificación** y **Análisis** con la carrera pre-cargada (`demo_seed`),
como en junio: splits, deriva cardiaca, distribuciones.

## 5. Pruebas de carga: así se mide el capítulo 8 (5 min)

> "Y esta es la novedad de fondo: la plataforma está medida. Lanzo el pico de
> un evento real, 800 mensajes por segundo, y hacemos la contabilidad."

```bash
$SIM --athletes 16 --speedup 50 --session-id demo_carga \
     --broker-host $BROKER --broker-port 31883 --log-level ERROR \
     | tee /tmp/carga.jsonl
```

(~90 s; mientras corre, el dashboard Live enseña el chorro: mensajes/s
subiendo a ~800. Buen momento para narrar el método.)

> "El simulador solo cuenta las lecturas que el broker le CONFIRMA, no las que
> encola: la carga ofrecida es real. Al terminar, contamos en la base de datos
> cuántas se persistieron para esta sesión, y la diferencia es la pérdida."

Al acabar, el veredicto:

```bash
python3 -c "import json; print('ofrecido :', sum(json.loads(l)['acked'] for l in open('/tmp/carga.jsonl')))"

curl -s --request POST "http://$BROKER:30086/api/v2/query?org=hyrox" \
  -H "Authorization: Token $INFLUX_TOKEN" -H "Accept: application/csv" \
  -H "Content-Type: application/vnd.flux" \
  --data 'from(bucket:"telemetry") |> range(start:-15m)
    |> filter(fn:(r)=>r._measurement=="biometrics" and r.session_id=="demo_carga" and r._field=="heart_rate")
    |> group() |> count()' | tail -2 | head -1 | awk -F, '{print "persistido:", $NF}'
```

Esperado: pérdida **<0,5%** (hoy en el ensayo: 0,30%).

> "Cuando empezamos a medir, este mismo nivel perdía el 45% de las lecturas.
> La evaluación encontró tres cuellos de botella reales, los arreglamos, y
> re-medimos: tres campañas en un día. Eso es el capítulo 8."

Si preguntan cómo son las campañas completas: `experiments/load-tests/run_matrix.sh`
recorre réplicas {1,2,4} × cargas {50..2000 msg/s} con este mismo método más el
procesador instrumentado (percentiles de latencia por ventana); resultados y
figuras en `experiments/results/2026*`.

## 6. Cierre (2 min)

```bash
kubectl get hpa processor -n hyrox      # ya habrá vuelto (o estará volviendo) a 1
(cd components/simulator && .venv/bin/pytest -q)
(cd components/processor && .venv/bin/pytest -q)
```

> "El autoescalador ha devuelto los recursos solo. Y todo lo de hoy está
> respaldado por 64 pruebas unitarias y por el repositorio: si esta máquina
> ardiera, la plataforma se reconstruye desde el repo más las imágenes."

Hecho vs siguiente: ✅ GitOps, HPA validado, evaluación con 3 campañas y mejoras
dirigidas por datos, procesador 0.5.0, tests · 🔜 gestión de secretos (Sealed
Secrets), techo del broker (multihilo o nodo dedicado), memoria.

## Plan B

- **Grafana caído**: `kubectl rollout restart deployment/grafana -n hyrox` (~30 s).
- **No entran datos**: casi siempre el reloj → repite el bucle de timesyncd.
- **El paso 2 no revierte**: `kubectl get application hyrox-platform -n argocd`
  debe decir Synced con auto-sync; si no, avísame antes de la demo.
- **El HPA arranca en 4**: has lanzado carga hace poco; espera ~6 min.
- **Sims residuales**: `pkill -f hyrox-sim` y limpio.
- **Capturas de respaldo** en `docs/figuras/cap7/` por si falla el directo.
