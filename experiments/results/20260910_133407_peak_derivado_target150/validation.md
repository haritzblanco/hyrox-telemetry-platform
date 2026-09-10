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

## La pérdida de estas corridas no es comparable entre sí

Las cifras de pérdida van de 16 % a 34 % sin seguir al tiempo de escalado, y la
razón es conocida: con 250 m una réplica rinde 191 msg/s o se descuelga, y de qué
lado cae depende de transitorios. En la primera corrida las cuatro réplicas
trabajaron entre el 56 y el 97 % de su cuota y el broker llegó a entregar
767 msg/s; en la segunda y la tercera se quedaron clavadas en el 99-100 %. La
pérdida mide sobre todo eso.

**El tiempo de escalado sí es comparable**, porque no depende del régimen en que
caigan las réplicas sino de cuándo decide KEDA, y es la magnitud que estas
corridas acotan. Para comparar pérdidas haría falta repetir cada configuración
varias veces y quedarse con la distribución, no con una corrida.

## Qué se cambió

`broker-exporter-0.3.0` deriva el caudal del contador acumulado en vez de leer la
media móvil, y el broker publica sus estadísticas cada 5 s en vez de cada 10
(`sys_interval`). El objetivo de 150 msg/s por réplica **no** se ha dejado puesto:
es lo que corresponde a una réplica estrangulada a 250 m, no a la de producción.
El ScaledObject sigue en 400, que es lo correcto para el límite real.
