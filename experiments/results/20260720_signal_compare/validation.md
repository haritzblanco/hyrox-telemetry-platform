# Caudal vs CPU como señal de autoescalado — 2026-07-20

Aísla la aportación específica de KEDA frente al HPA por CPU anterior. La
reducción de pérdida del pico (15% con 1 réplica fija → <1% escalando) es
mérito del **autoescalado horizontal**, no de KEDA: cualquier autoescalador que
llegue a las réplicas necesarias lo consigue. Lo que KEDA cambia es la **señal**
que decide cuántas réplicas, y eso es lo que se mide aquí.

## Metodología

Una sola corrida por nivel de carga contra el ScaledObject de producción **sin
modificarlo** (dispara `max(caudal, cpu)`). En cada instante se registran las
dos señales a la vez —caudal del exporter del broker y CPU del procesador— y se
calcula, para cada una por separado, cuántas réplicas ordenaría:

- caudal (metrics-api, AverageValue): `réplicas = techo(caudal / 400)`.
- cpu (HPA Utilization 75% sobre request 200m): `réplicas = techo(CPU_total / 150m)`.

La comparación sale así de la misma corrida, sin la varianza entre ejecuciones
del testbed (host-bound: 3 VMs sobre 4 núcleos físicos). No se hicieron dos
corridas con configuraciones distintas porque habría exigido editar el
ScaledObject (gestionado por ArgoCD) y además mezclaría esa varianza.

Datos crudos: `samples_r800.csv`, `samples_r1200.csv`. Reproducir con
`compare_signals.py` + `analyze_signals.py` (en `experiments/load-tests/`).

## Resultado principal: dimensionado

| Carga real | caudal en régimen | caudal ordena | CPU en régimen | CPU ordena |
|------------|-------------------|---------------|----------------|------------|
| ~750 msg/s | 751 msg/s         | **2 réplicas**| 677 m          | **4 réplicas** |
| ~1120 msg/s| 1117 msg/s        | **3 réplicas**| 689 m          | **4 réplicas** |

El caudal es **proporcional a la carga** (2 réplicas a 750 msg/s, 3 a 1120) y se
lee en el mismo eje que la evaluación: atletas simultáneos. La CPU **satura a 4
en los dos niveles**: no distingue 750 de 1120 msg/s.

El motivo es medible. El procesador consume bajo carga entre ~500 y ~1000 m por
réplica (varía con la contención del host), muy por encima de su request de
200m, así que la utilización supera el 75% ya con 1 réplica y el HPA empuja al
máximo con cualquier carga real. La CPU no aporta información de dimensionado en
este testbed; el caudal mide la carga ofrecida directamente y no depende de la
contención del nodo.

## Consecuencia en la pérdida (referencia de réplica fija, writer 0.5.0)

Campaña `20260712_170312`, mismo procesador desplegado, réplicas fijas:

| Carga    | 1 réplica | 2 réplicas | 4 réplicas |
|----------|-----------|------------|------------|
| 400 msg/s| 0,17%     | 0,21%      | 0,48%      |
| 800 msg/s| 15,42%    | **0,30%**  | 0,92%      |

A 800 msg/s el caudal ordena 2 réplicas, que la referencia da como suficientes
(0,30%). La CPU ordena 4: funciona, pero no mejora la pérdida (0,92%) y gasta el
doble de réplicas que en este clúster pequeño compiten por CPU. El caudal
right-sizea; la CPU sobreaprovisiona.

## Lo que KEDA NO mejora (honesto)

- **Reacción al escalón**: el caudal es una media móvil de 1 min y va con
  retardo. A 800 msg/s la CPU cruzó a 2 réplicas en t=20 s y el caudal en t=50 s.
  El caudal no reacciona antes; por eso el ScaledObject conserva el disparador de
  CPU de respaldo, que cubre la reacción rápida.
- **Pérdida absoluta**: domina la rampa inicial y la contención del host. La
  misma carga de 800 msg/s dio 5,64% de pérdida en esta corrida y 0,09% hace una
  semana (`20260716_keda`). Esa varianza es precisamente por qué el dimensionado
  se argumenta con la referencia de réplica fija y no con una cifra en vivo.

## Conclusión

El valor demostrado de KEDA aquí es el **dimensionado**: el número de réplicas
sigue a la carga ofrecida (2 a 800, 3 a 1200), interpretable en atletas, mientras
que la CPU satura al máximo y sobreaprovisiona porque el uso real por réplica
excede el request. La reacción rápida la sigue dando el disparador de CPU de
respaldo. No se afirma que KEDA reduzca la pérdida frente a un HPA por CPU bien
ajustado en este testbed; se afirma que right-sizea y que la señal es la
correcta para leer capacidad.
