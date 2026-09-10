# ¿Existe un límite de CPU que cumpla los requisitos escalando? — 2026-09-09

Resultado **negativo**, y por eso mismo útil: descarta rediseñar el capítulo en
torno a una sola matriz.

**La pregunta.** El capítulo presenta hoy dos matrices, una con las réplicas
estranguladas (la ley de escalado) y otra con el límite de producción (dónde cae
el pico de diseño). ¿Se puede sustituir ambas por una sola, eligiendo un límite
por réplica tal que una instancia NO cumpla los requisitos al pico y varias sí?
Si existiera, ese límite pasaría a ser la decisión de dimensionado de producción
y la evaluación mediría la configuración desplegada, sin nada artificial que
explicar.

**Medidas, todas con límite de 300 m por réplica** (imagen `processor-0.9.0`,
registro INFO, Mac al 80 % de idle, relojes verificados):

| Réplicas | Carga | Pérdida | Transporte p95 | CPU/réplica |
|---|---|---|---|---|
| 1 | 400 | 22,97 % | 44.653 ms | 268 m |
| 1 | 600 | 38,44 % | 47.617 ms | 264 m |
| 1 | 800 | 54,32 % | 53.622 ms | 261 m |
| 2 | 800 | 21,93 % | 41.508 ms | 249 m |
| 4 | 800 | **0,96 %** | **1.369 ms** | 259 m |

Umbrales: pérdida < 0,5 % y latencia p95 < 2.000 ms.

## Por qué no sale

**La ventana es demasiado estrecha.** Con 300 m la capacidad sostenida es de unos
165-200 msg/s por réplica sea cual sea la topología, de modo que cuatro réplicas
suman justo el pico de diseño y lo atienden **sin margen**: 0,96 % de pérdida,
que incumple el umbral del 0,5 %, y 1.369 ms de latencia, que lo cumple pero con
un factor de 1,5. Para que cuatro réplicas pasaran con holgura haría falta subir
el límite a unos 450 m, y a esa altura una sola réplica vuelve a estar en
condiciones de absorber el pico: la calibración del 7 de septiembre midió 806
msg/s con una réplica limitada a 500 m gastando 365. Es decir, entre "cuatro
cumplen con margen" y "una no cumple" apenas queda recorrido.

**Y ese recorrido no se puede apuntar con precisión, porque el estrangulamiento
no es reproducible entre días.** Una réplica limitada a 175 m absorbió 404 msg/s
el 7 de septiembre gastando 180 m; hoy, con 300 m, la misma carga de 400 msg/s da
un 23 % de pérdida. La diferencia no está en el límite sino en si la réplica
sobrevive a la ráfaga inicial: la carga entra de golpe al abrirse todas las
conexiones, y si la instancia se descuelga en los primeros segundos entra en el
régimen degradado, donde cada mensaje cuesta unas cuatro veces más, y ya no
recupera mientras dure la corrida. Cerca del borde, el resultado es una moneda al
aire.

## Consecuencia

Se mantiene la estructura de dos matrices. No es una comodidad de redacción: en
este banco de pruebas **no existe un límite por réplica que a la vez haga
necesario el escalado y permita cumplir los requisitos con margen**. Medir la ley
de escalado y medir el cumplimiento de los requisitos exige, por tanto, dos
configuraciones distintas, y el capítulo hace bien en separarlas.

Coste de la comprobación: cinco corridas, unos cincuenta minutos.

## Segunda tentativa: 500 m (2026-09-09, `results/20260909_202446`)

Se repitió el contraste con el límite en 500 m, buscando dar margen a las
configuraciones que deben cumplir. Las tres celdas, todas al pico de diseño:

| Réplicas | Pérdida | Transporte p95 | Persistencia p95 | e2e | CPU/réplica |
|---|---|---|---|---|---|
| 1 | 36,50 % | 25.567 ms | 670 ms | 26.237 ms | 486 m |
| 2 | **0,38 %** | 8.315 ms | 896 ms | **9.211 ms** | 386 m |
| 4 | **0,76 %** | 571 ms | 1.368 ms | **1.939 ms** | 299 m |

**Tampoco sale, y ahora se ve por qué con claridad.** Ninguna configuración cumple
los dos umbrales a la vez: una réplica falla en ambos; dos cumplen la pérdida pero
incumplen la latencia por un factor de 4,6, señal de que no van al día y de que su
0,38 % solo se sostiene porque la espera de drenaje deja pasar el atasco; y cuatro
cumplen la latencia por los pelos, 1.939 ms de 2.000, pero incumplen la pérdida.

**El obstáculo de fondo es el sobrecoste de replicar.** Para servir esos mismos 800
msg/s, el consumo total pasa de 486 m con una réplica a 772 con dos y a 1.196 con
cuatro. Cada vez que se dobla la cuenta, atender la misma carga cuesta alrededor
de un 50 % más de CPU, y ese sobrecoste es justo el margen que haría falta. Por eso
el problema no está en acertar con el valor del límite: con 300 m cuatro réplicas
se quedan sin margen y con 500 m siguen sin tenerlo, mientras que subirlo lo
bastante para dárselo devuelve a una sola réplica la capacidad de cubrir el pico.

Se cierra aquí la línea: dos límites probados, 300 y 500 m, que acotan el rango
por ambos lados. Coste total de la comprobación: ocho corridas, unos ochenta
minutos.

## Tercera tentativa: 700 m — **esta sí sale** (`results/20260909_203951` y `20260909_204552`)

| Réplicas | Corrida | Pérdida | Transporte p95 | e2e | CPU/réplica |
|---|---|---|---|---|---|
| 1 | 1ª | 27,55 % | 21.746 ms | 22.469 ms | 534 m |
| 1 | 2ª | 23,50 % | 20.959 ms | 21.769 ms | 692 m |
| 1 | 3ª | 23,45 % | 21.192 ms | 22.099 ms | — |
| 2 | 1ª | **0,37 %** | 389 ms | **1.278 ms** | 548 m |
| 2 | 2ª | **0,23 %** | 240 ms | **939 ms** | 496 m |
| 2 | 3ª | **0,20 %** | 396 ms | **1.261 ms** | 553 m |
| 4 | 1ª | 0,79 % | 306 ms | 1.142 ms | 299 m |

**Con el límite en 700 m por réplica, una instancia no cumple ninguno de los dos
umbrales al pico de diseño y dos los cumplen los dos.** Y aguanta la repetición:
tres corridas de cada configuración, con la pérdida de una réplica entre el 23 y
el 28 % y la de dos entre el 0,20 y el 0,37 %, siempre por debajo del 0,5 %; la
cota de extremo a extremo de dos réplicas se mueve entre 939 y 1.278 ms frente al
umbral de 2.000. Los márgenes son de 1,5 a 2,5 veces en pérdida y de 1,6 a 2,1 en
latencia: reales, aunque muy inferiores a los dos órdenes de magnitud que da la
configuración de producción actual.

**Queda un cabo suelto: la celda de cuatro réplicas pierde más que la de dos**,
un 0,79 % que incumple el umbral. No es ruido, porque se repite en los tres
límites ensayados: 0,96 % con 300 m, 0,76 % con 500 m y 0,79 % con 700 m. Con el
límite de producción, en cambio, las celdas de cuatro réplicas de la campaña del
10 de agosto pierden entre el 0,01 % y el 0,03 %. Antes de adoptar esta
configuración habría que entender ese comportamiento, porque tal como está diría
que pasar de dos a cuatro réplicas empeora la fiabilidad.

## Dónde se pierden las lecturas con cuatro réplicas (2026-09-09)

Repetida la celda de cuatro réplicas a 700 m: 0,79 %, 0,58 % y 0,62 %. La segunda
y la tercera con los pods ya calientes, así que no es un efecto del arranque. El
reparto en el tiempo tampoco lo es: contando lo persistido segundo a segundo, la
corrida entrega entre 785 y 800 lecturas por segundo de principio a fin, con un
déficit de cuatro o cinco lecturas por segundo repartido por igual. Y por atleta
lo mismo: los dieciséis pierden entre 13 y 34 lecturas, en torno al 0,6 % cada
uno.

**La pérdida no está en el broker.** La contabilidad lo sitúa sin ambigüedad:

| | s1 | s2 |
|---|---|---|
| Ofrecido (PUBACK del broker) | 63.990 | 63.990 |
| **Consumido por el procesado** | **63.990** | **63.990** |
| Confirmado por InfluxDB | 63.620 | 63.594 |
| Errores de escritura | **0** | **0** |
| Persistido (conteo en la base) | 63.620 | 63.594 |

El broker entrega absolutamente todo. Los 370 y 396 puntos que faltan se encolan
para escritura, **no se confirman y no producen ningún error**: desaparecen dentro
del camino de escritura sin dejar rastro. Con el límite de producción el mismo
camino pierde 16 puntos en la celda equivalente (0,03 %), de modo que el
estrangulamiento multiplica el efecto por veinticinco.

Esto **corrige el diagnóstico** que da hoy el capítulo, que atribuye toda la
pérdida al descarte en la cola del broker. Eso es cierto en las celdas saturadas,
donde el consumido queda muy por debajo del ofrecido, pero no en estas, donde
coinciden. Localizar la causa exige capturar el código de respuesta de cada lote
contra InfluxDB, lo que pide tocar el `writer` y reconstruir la imagen.

## Cuarta tentativa: 750 m (`results/20260909_210941`)

| Réplicas | Pérdida | Transporte p95 | e2e |
|---|---|---|---|
| 1 | 2,04 % | 10.688 ms | 11.294 ms |
| 2 | 0,20 % | 316 ms | 1.069 ms |
| 4 | 0,65 % | 549 ms | 1.460 ms |

**750 m es peor elección que 700 m** aunque el número sea más redondo. Con 700 m
una réplica falla de forma rotunda y estable, entre el 23 y el 28 % de pérdida en
tres corridas; con 750 se queda en el 2,04 %, que sigue incumpliendo pero por poco.
Cincuenta milicores más mueven la pérdida de una réplica de un 25 % a un 2 %, lo
que sitúa el punto de operación justo en el filo del cambio de régimen: una
repetición podría caer del otro lado y dejar el capítulo sin el contraste que
justifica la matriz.
