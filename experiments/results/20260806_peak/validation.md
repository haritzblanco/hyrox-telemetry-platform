# Corridas al pico de diseño con la configuración de producción — 2026-08-06

Miden las tres magnitudes con umbral del capítulo de requisitos sobre la
plataforma tal y como la despliega ArgoCD, sin fijar réplicas a mano: manda el
ScaledObject de KEDA. Motivo de la campaña: la validación de KEDA del
2026-07-16 dio la cifra de pérdida al pico de diseño, pero no llevaba
instrumentación de latencia, de modo que el umbral del RNF-2 (p95 < 2 s) no
tenía ninguna corrida que lo sostuviera en esas condiciones.

Arnés: `experiments/load-tests/run_peak.sh` y `analyze_peak.py`.
Carga: 40 atletas × speedup 20 ≈ 800 msg/s nominales (159.787 lecturas
confirmadas por el broker en cada tanda), generador multi-atleta en el Mac.
Relojes NTP verificados antes y después (offsets crudos de 232 a 261 ms,
`multipass exec` incluido), de modo que la latencia de transporte es fiable.

## Resultados

| Tanda | Arranque | Pérdida | Transporte p95 | Persistencia p95 | e2e (cota sup.) |
|---|---|---|---|---|---|
| `20260806_204127_peak` | en frío (1 réplica) | 1,44 % | 19.480 ms | 747 ms | 20.227 ms |
| `20260806_204757_peak_warm` | en caliente (4 réplicas) | **0,00 %** (1 lectura) | 5.228 ms | 703 ms | 5.931 ms |
| `20260806_205403_peak_attrib` | en caliente (4 réplicas) | 0,79 % | 11.860 ms | 650 ms | 12.509 ms |

Umbrales: pérdida < 0,5 % y latencia p95 < 2.000 ms.

- **RNF-3 (pérdida).** Se cumple con margen amplio en la tanda en caliente
  (una sola lectura perdida de 159.787) y se incumple cuando el escalado
  arranca en frío. La pérdida es descarte en la cola del broker, no fallo de
  escritura: en las tres tandas el procesador persiste todo lo que consume
  (0 errores de escritura) y lo que falta nunca llegó a consumirse.
- **RNF-2 (latencia).** No se cumple en ninguna de las tres. La persistencia se
  mantiene siempre por debajo de 750 ms, en línea con el medio segundo de
  escritura por lotes; el exceso está íntegramente en el transporte.

## Dónde está el retardo

La tanda `_attrib` se lanzó con el publicador del simulador instrumentado para
cronometrar cada publicación hasta su PUBACK. El resultado descarta al
generador y acota el problema:

| Tramo | p95 | Qué cubre |
|---|---|---|
| publicación → PUBACK (medido en el simulador) | **61 ms** | cola local del cliente e ida y vuelta hasta el broker |
| emisión → consumo (medido en el procesador) | **11.860 ms** | lo anterior más la entrega del broker al suscriptor |
| encolado → confirmación de InfluxDB | 650 ms | escritura por lotes |

La lectura entra en el broker en 61 ms y el procesador la recibe casi 12 s
después. El retardo está en la entrega del broker a los suscriptores de la
suscripción compartida, no en la red, ni en el generador, ni en la escritura.

Las demás causas candidatas quedan descartadas por medida:

- **No es CPU del procesador.** Consumo de 146 a 168 mCPU de media por réplica,
  con picos de 321 m, frente a un límite de 1.000 m. Las réplicas están
  ociosas mientras esperan mensajes.
- **No es el reloj.** Offsets NTP de ~240 ms antes y después de las tandas.
- **No es la escritura.** 0 errores y persistencia p95 estable en 650-750 ms.
- **No es el generador.** PUBACK en 61 ms con la carga completa en marcha.

## La causa, confirmada por medida

`mosquitto.conf` no fija `max_inflight_messages`, así que rige el valor por
defecto de mosquitto: **20 mensajes QoS 1 en vuelo por cliente suscriptor**. El
caudal máximo hacia un suscriptor queda entonces acotado por esa ventana
dividida entre el tiempo de ida y vuelta de la confirmación, con independencia
de la CPU disponible. Es coherente con todo lo observado: réplicas ociosas y
saturadas en 200-280 msg/s cada una, cola del broker llenándose hasta descartar
y, sobre todo, con que en la campaña del 12 de julio la pérdida a 800 msg/s
cayera del 45 % al 13 % al pasar de 1 a 4 réplicas sin que la CPU fuese el
límite: cada réplica añade su propia ventana de 20.

La comprobación fue directa: `max_inflight_messages 500` en la configuración del
broker, reinicio de mosquitto y repetición de las dos tandas, sin tocar ninguna
otra pieza. El resultado no deja lugar a interpretación.

| Tanda | Ventana | Pérdida | Transporte p95 | e2e (cota sup.) |
|---|---|---|---|---|
| en frío | 20 (defecto) | 1,44 % | 19.480 ms | 20.227 ms |
| en frío | **500** | **0,07 %** | **114 ms** | **797 ms** |
| en caliente | 20 (defecto) | 0,00 % | 5.228 ms | 5.931 ms |
| en caliente | **500** | **0,06 %** | **118 ms** | **912 ms** |

La latencia de transporte cae en un factor de 170 y deja de depender de si el
escalado arranca en frío: con la ventana holgada, la tanda en frío rinde igual
que la que arranca con las cuatro réplicas en pie, porque una réplica ya no
está limitada a 20 mensajes sin confirmar. Con el ajuste, **ambos umbrales se
cumplen al pico de diseño en la configuración de producción**, y se cumplen en
el caso peor, que es el arranque en frío.

El consumo de CPU confirma la lectura: las réplicas siguen entre 131 y 162 mCPU
de media, igual que antes del cambio. Nunca fue un problema de capacidad de
cómputo, sino una ventana de protocolo que mantenía ociosas a las réplicas.

El techo que la campaña de julio atribuyó al enrutado en un solo hilo de
mosquitto era, por tanto, este parámetro. La línea de trabajo futuro hacia un
broker con enrutado en paralelo (EMQX, VerneMQ) sigue siendo válida para cargas
muy por encima del pico de diseño, pero ya no es lo que limitaba a la
plataforma en el rango evaluado.

El ajuste queda incorporado al ConfigMap del chart
(`infra/helm/hyrox-platform/charts/mosquitto/templates/configmap.yaml`), de modo
que ArgoCD lo aplica en cuanto el cambio se publique en el repositorio.

## Estado del clúster

Estas tandas se ejecutaron con una ventana de experimento abierta: sincronía
automática suspendida en las Applications `root` y `hyrox-platform`, y el
Deployment del procesador parcheado con `--metrics-interval 10`. Al cerrar la
ventana se restaura `syncPolicy.automated` en ambas y ArgoCD revierte el
Deployment al estado del repositorio.
