# Qué frena al autoescalado: la señal y el umbral — 2026-09-10

Cuatro corridas de arranque en frío con la misma carga (40 atletas × speedup 20,
unos 800 msg/s), el mismo límite (250 m por réplica), el mismo arranque desde una
réplica y la imagen `processor-0.11.0`. Solo cambia con qué decide KEDA.

| Señal de caudal | Objetivo | 2 réplicas | 4 réplicas | Pérdida | Dataset |
|---|---|---|---|---|---|
| media móvil de 1 min | 400 | 28 s | 72 s | 16,06 % | `20260910_105648_peak` |
| media móvil de 1 min | 150 | 46 s | 81 s | 33,89 % | `20260910_131609_peak_target150` |
| derivada del contador | 400 | 19 s | 118 s | 34,15 % | `20260910_132923_peak_caudal_derivado` |
| derivada del contador | 150 | 37 s | **65 s** | 22,95 % | esta |

## Eran dos frenos, y cada uno tapaba al otro

**La señal llegaba tarde.** El exportador servía
`$SYS/broker/load/publish/received/1min` entre 60, la media móvil que calcula
mosquitto. A los 20 s de corrida marcaba 51 msg/s con 800 ofrecidos. Derivando el
contador acumulado, la tercera corrida marcó **795 msg/s a los 13 s**: la señal
pasa a ser la carga real casi al instante.

**El umbral estaba calibrado para otra réplica.** El ScaledObject apunta a 400
msg/s por réplica, que es lo razonable en producción, donde una réplica absorbe
853. Con 250 m el techo real es de unos 190.

Cada arreglo por separado no sirve, y la tercera corrida lo enseña de la forma
más clara: con la señal ya correcta pero el umbral en 400, KEDA calcula que
795 msg/s se cubren con dos réplicas y **se planta ahí**. Con dos réplicas la
media por réplica cae a 397, justo en el objetivo, o sea bien dimensionado según
él; a partir de ahí solo empuja el disparador de CPU, un escalón por ciclo, y
llegar a cuatro cuesta 118 s, lo peor de las cuatro corridas. La segunda corrida
enseña lo simétrico: el umbral correcto no sirve de nada si la señal no llega a
cruzarlo, porque a los 23 s valía 134,7 msg/s.

Con las dos piezas, llegar a cuatro réplicas cuesta **65 s en vez de 84**.

## Cuatro repeticiones de cada configuración

Una corrida por configuración no bastaba: con 250 m una réplica rinde 191 msg/s o
se descuelga, y de qué lado cae depende de transitorios. Se repitieron las dos
configuraciones extremas cuatro veces cada una, alternando solo la imagen del
exportador y el objetivo del disparador.

| | base (media de 1 min, objetivo 400) | arreglado (derivada, objetivo 150) |
|---|---|---|
| 2 réplicas (mediana) | 44 s | **28 s** |
| 4 réplicas (mediana) | 118 s | **60 s** |
| 4 réplicas (todas) | 72, 112, 125, 149 s | 46, 55, 65, 66 s |
| Pérdida (media) | 23,6 % | 20,8 % |
| Pérdida (todas) | 16,1 / 21,8 / 25,3 / 31,1 % | 16,7 / 21,3 / 22,1 / 22,9 % |

**El tiempo de escalado mejora sin ambigüedad.** Las cuatro corridas arregladas
alcanzan las cuatro réplicas antes que cualquiera de las cuatro de base: los dos
conjuntos no se solapan. La mediana pasa de 118 s a 60 s, algo menos de la mitad.

**La pérdida no permite afirmar tanto.** La media baja del 23,6 % al 20,8 % y la
dispersión se reduce mucho, de quince puntos de rango a seis, pero las dos
distribuciones se solapan y con cuatro corridas por configuración esa diferencia
de tres puntos no se sostiene. Lo que sí queda claro es que **la corrida original
de la base, con su 16,1 %, era la más afortunada de su grupo**: también fue la
más rápida en escalar, 72 s frente a una mediana de 118. Sacar conclusiones de
ella habría sido un error.

La razón de que la pérdida no siga al tiempo de escalado es la biestabilidad de
la réplica estrangulada: en las corridas buenas las cuatro réplicas trabajan
entre el 56 y el 97 % de su cuota y el broker llega a entregar 767 msg/s; en las
malas se quedan clavadas en el 99-100 % y entregan bastante menos. Esa lotería
pesa más en la pérdida que los segundos que tarde el autoescalador.

## Qué se cambió

`broker-exporter-0.3.0` deriva el caudal del contador acumulado en vez de leer la
media móvil, y el broker publica sus estadísticas cada 5 s en vez de cada 10
(`sys_interval`). El objetivo de 150 msg/s por réplica **no** se ha dejado puesto:
es lo que corresponde a una réplica estrangulada a 250 m, no a la de producción.
El ScaledObject sigue en 400, que es lo correcto para el límite real.
