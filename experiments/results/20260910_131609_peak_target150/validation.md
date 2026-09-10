# Bajar el objetivo de caudal de KEDA no acelera el escalado — 2026-09-10

Prueba para comprobar si el arranque en frío de `20260910_105648_peak` mejoraba
ajustando el disparador de caudal al techo real de la réplica estrangulada. El
ScaledObject apunta a 400 msg/s por réplica, pensado para la réplica de
producción; con 250 m el techo es de unos 190, así que la métrica nunca cruzaba
el objetivo y todo el escalado lo llevaba el disparador de CPU.

Se bajó el objetivo a 150 msg/s por réplica y se repitió la corrida con la misma
carga (40 atletas × speedup 20, unos 800 msg/s), el mismo límite (250 m) y el
mismo arranque desde una réplica. El objetivo se restauró a 400 al terminar.

## No mejora, y la comparación además está contaminada

| | objetivo 400 | objetivo 150 |
|---|---|---|
| 2 réplicas | 28 s | 46 s |
| 3 réplicas | 73 s | 74 s |
| 4 réplicas | 84 s | 81 s |
| Pérdida | 25.661 (16,06 %) | 54.158 (33,89 %) |

Llegar a cuatro réplicas cuesta lo mismo, 81 s frente a 84 s. La pérdida sale
peor, pero **no por el cambio de objetivo**: las dos corridas cayeron en
regímenes distintos. Con el objetivo a 400 las cuatro réplicas trabajaron entre
el 56 y el 97 % de su cuota y el broker llegó a entregar 767 msg/s; con el
objetivo a 150 se quedaron clavadas en el 99-100 % y entregaron bastante menos.
Es la misma biestabilidad que la calibración ya había medido: con la misma cuota,
una réplica rinde 191 msg/s o se descuelga, y de qué lado cae depende de
transitorios. La diferencia de pérdida mide eso, no el disparador.

## Lo que sí queda establecido

El disparador de caudal **tampoco se dispara con el objetivo en 150**. A los 23 s
de corrida la métrica valía 134,7 msg/s, todavía por debajo del objetivo, y a esas
alturas ya se ofrecían 800. El escalado volvió a llevarlo el disparador de CPU en
las dos corridas.

La causa es la métrica, no el umbral. El exportador publica
`$SYS/broker/load/publish/received/1min` dividido entre 60, que es la **media
móvil de un minuto** que calcula mosquitto. Con una corrida de 230 s, esa media
va siempre muy por detrás de la carga real: no puede avisar de un pico hasta que
el pico lleva medio minuto ocurriendo. Bajar el umbral no arregla un indicador
que llega tarde.

## Qué haría falta

Publicar un caudal instantáneo en vez de una media móvil: mosquitto también
expone el contador acumulado `$SYS/broker/publish/messages/received`, y derivarlo
entre dos lecturas separadas unos segundos daría una tasa que reacciona en
segundos en vez de en un minuto. Es un cambio en el exportador, no en KEDA.

Mientras tanto, la corrida válida para el capítulo sigue siendo
`20260910_105648_peak`, con la configuración de producción intacta.
