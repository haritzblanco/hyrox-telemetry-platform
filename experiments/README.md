# Evaluación experimental de la plataforma

Mide el comportamiento del pipeline de extremo a extremo bajo carga: **latencia**,
**throughput**, **consumo de recursos** y **respuesta al escalado horizontal** del
procesador (1 → N réplicas). El generador de carga es el propio **simulador** del
proyecto (no una herramienta externa): se lanzan N atletas sintéticos que publican
en MQTT a una tasa controlada.

## Qué se mide y cómo

| Métrica | Cómo | Reloj |
|---|---|---|
| **Latencia de transporte** (emisión → consumo) | El processor resta `now − payload.timestamp` por mensaje | Cruza Mac↔VM → **exige sincronía** (ver offset) |
| **Latencia de persistencia** (encolado → confirmación de InfluxDB) | Callback de batching del cliente InfluxDB, FIFO sobre marcas `monotonic()` | Intra-proceso → inmune al desfase |
| **Throughput** | Mensajes/s consumidos y confirmados por réplica (líneas JSON de métricas) | — |
| **Pérdida** | `ofrecido` (simuladores) − `persistido` (conteo en InfluxDB); y `consumido − confirmado` (fallos de escritura) | — |
| **Recursos** | `kubectl top pods` muestreado a intervalo fijo → CPU (m) y memoria (Mi) por réplica | — |
| **Marcas pendientes** | Puntos encolados para escritura y todavía sin confirmar (`persist_pending_min` por ventana) | — |

> ⚠️ **Qué significa el suelo de `persist_pending`, y qué significaba antes.**
> La cola sube y baja con cada lote y en régimen vuelve a rozar el cero; un suelo
> estable por encima de cero es un retraso que el escritor no recupera entre
> lotes, es decir, **cola de escritura real**, y se suma con razón a la latencia
> de persistencia.
>
> Hasta la imagen `processor-0.10.0` incluida ese mismo suelo contaba además
> **lecturas perdidas**: el procesador delegaba el lote en el cliente de InfluxDB,
> que descartaba puntos en silencio y dejaba sus marcas atrapadas para siempre,
> inflando toda medida posterior de esa réplica. `analyze.py` lo resume por
> corrida en `marcas_atrapadas`, que sigue siendo el indicador con el que leer las
> campañas de junio a septiembre de 2026. Diagnóstico en
> `results/20260909_212221_diag_writer/validation.md`; la causa raíz —una carrera
> entre el hilo de vaciado y el del productor en el operador de ventanas del
> cliente— en `results/20260910_101652_causa_raiz/validation.md`, reproducible sin
> clúster con `load-tests/bench_write_api.py`. Desde el lote propio el procesador
> agrupa y escribe él mismo, y ese descarte no puede darse.

La instrumentación del processor se activa con `--metrics-interval > 0` y emite una
línea JSON por ventana en **stdout** (`{"kind":"metrics",...}`), que el orquestador
recoge por `kubectl logs`. Con el intervalo a 0 (por defecto) el processor no cambia
su comportamiento en demo/producción.

> ⚠️ **Desfase de reloj Mac↔VM.** La memoria del proyecto avisa de que el reloj de la
> VM k3s va desfasado. La latencia de transporte solo es válida con ambos relojes
> sincronizados: `clock_sync.sh` sincroniza las VMs por NTP y mide el offset residual,
> que se registra en `results/<run>/clock_offset.txt` para descontarlo si hace falta.
> La latencia de **persistencia** no se ve afectada (se mide dentro del proceso).

## Requisitos previos

1. **VMs arrancadas**: `multipass start k3s-hyrox hyrox-worker`.
2. **Imagen instrumentada** `hyrox/processor:0.3.0` construida e importada en ambos
   nodos (mismo flujo sin registry que `infra/manifests/processor/README.md`, cambiando
   el tag a `0.3.0`).
3. **metrics-server** operativo (`kubectl top nodes` responde; k3s lo trae de serie).
4. Desplegar el Deployment de experimento (imagen 0.3.0 + métricas activas):
   ```bash
   kubectl apply -f experiments/load-tests/processor-exp.yaml
   ```

## Ejecutar la matriz

```bash
# Por defecto: réplicas {1,2,4} × atletas {4,8,12,16,20} con speedup 50
# (≈ 200..1000 msg/s). Procesa el clúster (LOCAL_PROCESSOR=false implícito).
bash experiments/load-tests/run_matrix.sh

# Personalizable por entorno:
REPLICAS="1 2 4" N_LIST="4 8 12 16 20" SPEEDUP=50 \
  bash experiments/load-tests/run_matrix.sh
```

Cada corrida `R{r}_N{n}` deja en `experiments/results/<timestamp>/<run>/`:
- `proc_<pod>.jsonl` — ventanas de métricas del processor (latencia/throughput).
- `resources.csv` — muestreo de CPU/memoria por réplica.
- `sim.jsonl` — conteo de lecturas ofrecidas por atleta.
- `run.json` — metadatos (réplicas, carga, ofrecido, persistido, ventana temporal).

## Llevar el cuello de botella al procesado

Con el límite de producción (1000 m por réplica) una sola réplica absorbe el pico
de diseño entero, así que la matriz sale plana: las tres configuraciones dan la
misma curva y no se puede observar qué aporta cada réplica. Por encima de 800
msg/s tampoco vale subir la carga, porque el que se satura primero es el Mac que
genera el tráfico, no el clúster.

`CPU_LIMIT` estrangula cada réplica antes de la tanda para que el límite esté en
el procesado, que es la variable que la matriz manipula. Con la capacidad medida
de una réplica (≈ 0,36 m de CPU por msg/s más unos 17 m fijos), un límite de
125 m sitúa su techo alrededor de 300 msg/s: una réplica satura ya a media carga,
dos se quedan cortas al pico y cuatro lo absorben.

```bash
# Matriz con réplicas estranguladas: la curva de caudal deja de ser plana
CPU_LIMIT=125m REPLICAS="1 2 4" N_LIST="4 8 12 16" \
  bash experiments/load-tests/run_matrix.sh

# El mismo estrangulamiento con KEDA al mando: el autoescalador recupera el caudal
CPU_LIMIT=125m bash experiments/load-tests/run_peak.sh
```

El límite original se restaura al terminar la tanda, incluso si se interrumpe.

## Caída de una réplica

Si una réplica basta para el pico, la segunda solo se justifica por
disponibilidad, y eso se mide: `run_failover.sh` congela dos réplicas, lanza el
pico y elimina una a mitad de corrida sin terminación ordenada, de modo que la
superviviente tenga que absorber el total mientras el planificador repone la
baja.

```bash
KILL_AT=90 bash experiments/load-tests/run_failover.sh
```

A diferencia del resto del arnés, esta corrida va siguiendo los logs de cada pod
desde el principio: los del pod eliminado desaparecen con él y no se pueden leer
al final.

## Analizar

```bash
python experiments/notebooks/analyze.py experiments/results/<timestamp>
# Genera figuras en results/<timestamp>/figures/ (latencia vs carga, techo de
# throughput, pérdida, CPU/mem vs réplicas) y un resumen agregado summary.csv.
```

## Estructura

```
experiments/
├── load-tests/
│   ├── processor-exp.yaml      Deployment con imagen instrumentada + métricas
│   ├── clock_sync.sh           Sincroniza relojes de las VMs y mide el offset
│   ├── sample_resources.sh     Muestreo de kubectl top → CSV
│   ├── collect_metrics.py      Extrae las líneas JSON de métricas por ventana
│   └── run_matrix.sh           Orquestador de la matriz réplicas × carga
├── notebooks/
│   └── analyze.py              Agregación y figuras
└── results/                    Salidas por corrida (generado)
```
