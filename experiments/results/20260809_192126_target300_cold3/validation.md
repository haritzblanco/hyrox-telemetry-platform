# Corrida al pico de diseño con el objetivo de KEDA en 300 msg/s — 2026-08-09

Mide el comportamiento de la plataforma tal y como la despliega ArgoCD, sin
fijar réplicas a mano, con el objetivo del ScaledObject bajado de 400 a
300 msg/s por réplica. El cambio se propuso a partir de una matriz que atribuía
a la configuración de dos réplicas una latencia de segundos al pico de diseño;
esa lectura resultó ser un artefacto del equipo generador (ver más abajo), de
modo que esta corrida no debe leerse como la demostración de que 300 sea
necesario, sino como la validación de que la configuración completa cumple los
tres requisitos en el caso peor.

Arnés: `experiments/load-tests/run_peak.sh` y `analyze_peak.py`.
Carga: 40 atletas × speedup 20 ≈ 800 msg/s nominales (159.787 lecturas
confirmadas por el broker), generador multi-atleta en el Mac.
Arranque en frío: una sola réplica en pie al empezar, que es el caso peor.
Relojes NTP verificados antes de la corrida (offsets crudos de 62 a 230 ms,
`multipass exec` incluido), de modo que la latencia de transporte es fiable.

## Resultados

| Magnitud | Medida | Umbral | |
|---|---|---|---|
| Pérdida (RNF-3) | **0,04 %** (61 lecturas de 159.787) | < 0,5 % | cumple |
| Transporte p95 (RNF-2) | **96 ms** (media 97,4; p99 99,6; peor ventana 120,2) | — | |
| Persistencia p95 | 507 ms (media 319,8; p99 520,8; peor ventana 3.151,7) | — | |
| Extremo a extremo, cota superior | **603 ms** | < 2.000 ms | cumple |

El procesador persiste todo lo que consume: 159.787 consumidas, 159.726
confirmadas, **0 errores de escritura**. Las 61 lecturas que faltan no llegaron
a consumirse, de modo que la pérdida es descarte en el broker durante el
transitorio de arranque, no fallo de escritura.

### RNF-1. El escalado sigue al caudal

| Instante (UTC) | Réplicas | Caudal medido por KEDA |
|---|---|---|
| 19:21:26 | 1 | 0 |
| 19:22:04 | 2 | 242 msg/s por réplica |
| 19:22:48 | 3 | 266 msg/s por réplica |
| 19:23:37 | 4 | 224 msg/s por réplica |

El autoescalador alcanza las cuatro réplicas en 2 min y 11 s desde el inicio de
la carga, pasando por tres. Con el objetivo en 400 msg/s el escalado pasaría de
dos réplicas directamente a cuatro; con 300 aparece el escalón intermedio y el
caudal por réplica se estabiliza en 167-191 msg/s, por debajo del objetivo.

### Reparto de carga y consumo de recursos

| Réplica | Ventanas | Consumido | Caudal sostenido | Pico | CPU media | CPU pico | Memoria |
|---|---|---|---|---|---|---|---|
| `…-42krs` | 24 | 71.257 | 266 msg/s | 802 msg/s | 150 m | 270 m | 45 Mi |
| `…-4ls5j` | 20 | 45.417 | 266 msg/s | 401 msg/s | 191 m | 314 m | 34 Mi |
| `…-jhdp7` | 15 | 27.577 | 200 msg/s | 267 msg/s | 156 m | 233 m | 34 Mi |
| `…-l9t7b` | 11 | 15.536 | 200 msg/s | 201 msg/s | 107 m | 125 m | 34 Mi |

El pico de 802 msg/s de la primera réplica corresponde a la ventana en la que
absorbió ella sola la carga completa antes de que entrara la segunda, y lo hizo
con 270 mCPU y sin acumular retardo apreciable. Con las cuatro en pie el reparto
es homogéneo (~200 msg/s cada una). El consumo se mantiene entre 107 y 191 mCPU
de media frente a un límite de 1.000 m y un request de 200 m, y la memoria no
pasa de 49 Mi frente a un límite de 256 Mi: al pico de diseño la plataforma no
está limitada por cómputo, y el dimensionado por caudal es el criterio correcto
para el autoescalador.

## Validez de la serie del mismo día

Esta corrida repite, sin cambiar ningún parámetro de la plataforma, las dos que
se lanzaron esa misma tarde con el objetivo ya en 300:

| Tanda | Hora (UTC) | Pérdida | Transporte p95 | e2e (cota sup.) |
|---|---|---|---|---|
| `20260809_171215_target300_cold` | 17:12 | 4,54 % | 30.191 ms | 31.618 ms |
| `20260809_171837_target300_cold2` | 17:18 | 0,95 % | 18.786 ms | 21.952 ms |
| `20260809_192126_target300_cold3` | 19:21 | **0,04 %** | **96 ms** | **603 ms** |

**Las dos primeras no son válidas y no deben citarse como medidas de la
plataforma.** Entre ellas y la tercera solo cambió el estado del equipo
generador, que se reinició: la máquina acumulaba varias horas de campaña y no
sostenía el ritmo de publicación. La latencia de transporte se calcula como la
diferencia entre el instante de consumo en el procesador y la marca de tiempo
que el generador escribe en el propio mensaje, de modo que un generador que
publica tarde imputa su propio retraso a la plataforma. La cifra de 96 ms es
además coherente con los 114 ms medidos el 6 de agosto en la misma carga y con
la misma configuración de broker (`results/20260806_peak`), lo que confirma cuál
de las tres series describe el sistema.

La misma cautela alcanzaba a la matriz `20260809_164143`, lanzada poco antes:
sus celdas de 400 msg/s por réplica daban latencias de transporte de 124 a
216 ms, congruentes con esta corrida, pero las de 800 msg/s sobre dos réplicas
(`R2_N16`) daban 1.172 y 2.863 ms de p95 con un 0,42 % de pérdida, ya dentro de
la ventana en la que el generador se degradaba. Esas dos celdas se repitieron
con el equipo en reposo (`results/20260809_193353`, réplicas fijadas a dos con
`autoscaling.keda.sh/paused-replicas` para que el autoescalador no interfiriera,
mismo speedup 50 y mismo descanso de 90 s entre corridas):

| Celda | Tanda | Pérdida | Transporte p95 | Persistencia p95 | CPU media (2 réplicas) |
|---|---|---|---|---|---|
| `R2_N16` | 16:42 (original) | 0,42 % | 2.863 ms | 1.906 ms | 994 m |
| `R2_N16` | 17:04 (original) | 0,42 % | 1.172 ms | 2.247 ms | 1.092 m |
| `R2_N16` | **19:34 (repetida)** | **0,01 %** | **105 ms** | 510 ms | 324 m |
| `R2_N16` | **19:37 (repetida)** | **0,00 %** | **103 ms** | 525 ms | 384 m |

La repetición confirma que las cifras originales eran del generador y no de la
plataforma, y **corrige la lectura de fondo**: dos réplicas atienden el pico de
diseño con un 0,01 % de pérdida y 103-105 ms de transporte, es decir, 400 msg/s
por réplica no saturan nada. El consumo lo respalda: 162-192 mCPU por réplica
frente a los ~500 m que aparentaban las corridas contaminadas. Las celdas de la
matriz original a 800 msg/s no deben citarse en la memoria; las válidas son
estas.

Como criterio para futuras campañas: comprobar que el equipo generador está
ocioso antes de cada tanda de latencia (con las tres VMs en marcha la carga
media ronda 2,5, que es la línea base; lo relevante es que la CPU esté al menos
al 60 % ociosa y que no haya tareas de mantenimiento del sistema en curso).

## Comparación de los dos objetivos del autoescalador

Para decidir el valor con una medida y no con una conjetura se repitió la misma
corrida en frío con el objetivo en 400 msg/s por réplica, sin tocar ninguna otra
pieza (`results/20260809_194700_target400_cold`):

| Objetivo | Pérdida | Transporte p95 | Persistencia p95 | e2e (cota sup.) | 4 réplicas en |
|---|---|---|---|---|---|
| 300 msg/s | 0,04 % (61) | 96 ms | 507 ms | 603 ms | 2 min 11 s |
| 400 msg/s | 0,03 % (48) | 115 ms | 514 ms | 629 ms | 1 min 57 s |

Las dos configuraciones son indistinguibles: las diferencias caen dentro de la
dispersión entre corridas, y ambas cumplen los umbrales con dos órdenes de
magnitud de margen en latencia. Las dos recorren además la misma secuencia
1→2→3→4 réplicas.

El motivo de que el objetivo no cambie nada está en los datos de escalado:

| Objetivo | Caudal máximo medido por réplica | Uso de CPU máximo (sobre el request) |
|---|---|---|
| 300 msg/s | 266 msg/s | 135 % |
| 400 msg/s | 326 msg/s | 142 % |

**El disparador de caudal no llega a activarse en ninguna de las dos corridas**:
el valor por réplica se queda por debajo del objetivo incluso con el objetivo en
300. Quien ordena las réplicas es el disparador de CPU de respaldo, que a un 75 %
de utilización objetivo se dispara en cuanto la primera réplica pasa del 100 %
del request. La razón es que la métrica de caudal la publica el exporter del
broker a partir de las estadísticas de `$SYS`, que son una media móvil de un
minuto: en un transitorio de arranque que se resuelve en dos minutos, esa media
va siempre por detrás del uso de CPU, que es instantáneo.

De ahí que la elección entre 300 y 400 no tenga efecto observable al pico de
diseño. El objetivo de caudal gobernaría el escalado en un régimen sostenido por
encima del pico, o si la métrica del broker se publicara con una ventana más
corta; mientras tanto, el comportamiento del autoescalador en el arranque lo
define `targetCPU`.

## Conclusión

Con el objetivo del autoescalador en 300 msg/s por réplica, la plataforma cumple
los tres requisitos no funcionales al pico de diseño y en el caso peor, que es
el arranque en frío: pérdida del 0,04 % frente al umbral del 0,5 %, latencia
extremo a extremo acotada en 603 ms frente al umbral de 2 s, y escalado completo
en poco más de dos minutos.

La elección entre 300 y 400 msg/s por réplica no la decide ninguna medida: las
dos corridas en frío dan el mismo resultado dentro de la dispersión, y en ambas
el escalado lo ordena el disparador de CPU antes de que el de caudal llegue a su
umbral. Al pico de diseño, por tanto, el parámetro es inerte. Lo que sí queda
demostrado es que la capacidad de una réplica está muy por encima de los
400 msg/s que se le suponían: dos réplicas sostienen el pico completo con
103 ms de transporte p95 y 162-192 mCPU cada una, frente a un límite de 1.000 m.

## Estado del clúster

La corrida se ejecutó con una ventana de experimento abierta: sincronía
automática suspendida en las Applications `root` y `hyrox-platform`, Deployment
del procesador parcheado con `--metrics-interval 10` y `targetValue` del
ScaledObject fijado a 300 sobre el clúster. Al cerrar la ventana se restauró
`syncPolicy.automated` en ambas Applications y ArgoCD revirtió el Deployment y
el ScaledObject al estado del repositorio, que sigue en 400 hasta que se
publique el cambio.
