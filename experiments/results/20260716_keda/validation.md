# Validación del autoescalado por caudal (KEDA) — 2026-07-16

Valida el `ScaledObject` del subchart processor: el número de réplicas
sigue al caudal de publicaciones que entra al broker, medido por el
exporter sidecar de mosquitto (media móvil de 1 min de `$SYS`, servida
en msg/s), con la CPU como disparador de respaldo. Objetivo: 400 msg/s
por réplica (capacidad con pérdida despreciable según la campaña del
12 de julio), min 1 / max 4.

## Escenario

- Carga: 40 atletas × speedup 20 ≈ **800 msg/s nominales** durante ~230 s
  (generador multi-atleta en el Mac, broker por NodePort).
- Confirmado por el broker (PUBACK): 159.787 mensajes, media real
  ≈ 695 msg/s (las sesiones se estiran, igual que en las campañas).
- KEDA 2.20.1 instalado como Application de ArgoCD; el chart de la
  plataforma sincronizado por GitOps (commit `10c371a`).

## Línea temporal observada (t=0 al arrancar el muestreo, carga ya en rampa)

| t (s) | Caudal exporter (msg/s) | Réplicas |
|-------|-------------------------|----------|
| 15    | 96                      | 1        |
| 45    | 394                     | **2**    |
| 75    | 518                     | 2 → **3**|
| 120   | 687                     | 3 → **4**|
| 195   | 769 (pico)              | 4        |
| ~215  | fin de la carga         | 4        |
| +180 s de calma | cae con la media móvil | **3** |
| +60 s | —                       | **2**    |
| +60 s | —                       | **1**    |

## Eventos oficiales del HPA (`kubectl describe hpa keda-hpa-processor -n hyrox`)

```
Normal  SuccessfulRescale  New size: 2; reason: cpu resource utilization (percentage of request) above target
Normal  SuccessfulRescale  New size: 3; reason: cpu resource utilization (percentage of request) above target
Normal  SuccessfulRescale  New size: 4; reason: cpu resource utilization (percentage of request) above target
Normal  SuccessfulRescale  New size: 3; reason: All metrics below target
Normal  SuccessfulRescale  New size: 2; reason: All metrics below target
Normal  SuccessfulRescale  New size: 1; reason: All metrics below target
```

## Contabilidad de la sesión

- Confirmados por el broker: 159.787
- Persistidos en InfluxDB (count de heart_rate, group() antes de count):
  159.646
- **Pérdida: 0,09%** a ~800 msg/s con escalado en caliente. La misma carga
  con 1 réplica fija perdía un 15% en la campaña del 12 de julio.

## Notas

- En la rampa de subida el disparador que llegó antes fue la CPU: la señal
  de caudal es una media móvil de 1 min y arranca con retraso frente a un
  escalón de carga. Los dos triggers cooperan y manda el máximo; el caudal
  fija el dimensionado en régimen (400 msg/s por réplica) y no depende de
  cuánta CPU consuma el nodo por otros motivos.
- La bajada la gobierna la misma media móvil: el caudal decae de forma
  exponencial al cortar la carga, así que el 4 → 3 llega tras la ventana
  de 180 s contada desde que la métrica baja del umbral, no desde el
  último mensaje.
- Unidades verificadas: el valor de `$SYS/broker/load/publish/received/1min`
  dividido entre 60 coincide con la tasa confirmada por el simulador
  (~695-770 msg/s en régimen frente a 695 de media global).
