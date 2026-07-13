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
