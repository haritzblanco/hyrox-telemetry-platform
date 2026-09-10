# ¿Por qué la primera celda pierde más? — 2026-09-08

Repetición controlada de la celda `R1_N4` (una réplica, 200 msg/s) para contrastar
la hipótesis de que su pérdida elevada, un 0,14 % en la campaña del 10 de agosto,
se deba a que el sistema no está listo cuando empieza a llegar tráfico.

**Premisa que hay que corregir primero.** El arnés no arranca el clúster junto al
simulador. El clúster está en marcha de forma continua —en esta prueba llevaba
14 horas en pie y ocioso— y cada corrida espera a que las réplicas estén listas
(`rollout status`) más un margen de asentamiento antes de generar carga. Lo único
que sí ocurre a la vez al empezar cada corrida es la apertura de las conexiones
de los atletas, una por cada uno.

**Diseño.** Tres corridas idénticas y consecutivas sobre un clúster ocioso desde
hacía 14 h, con una réplica fijada y sin reinicios entre ellas.

| Corrida | Ofrecido | Persistido | Pérdida |
|---|---|---|---|
| 1ª | 15.885 | 15.873 | 12 lecturas, 0,076 % |
| 2ª | 15.885 | 15.883 | 2 lecturas, 0,013 % |
| 3ª | 15.885 | 15.876 | 9 lecturas, 0,057 % |

**La hipótesis no se sostiene.** Si el problema fuese el calentamiento, la primera
corrida perdería y las siguientes no. La primera es la peor, pero la tercera
pierde casi lo mismo, y las tres se mueven en el mismo orden de magnitud.

**Y la pérdida no está al principio.** Contando lo persistido en cubos de un
segundo, las tres corridas dan 200 lecturas por segundo desde el primer segundo
completo, sin ningún déficit inicial; el resto de la corrida oscila entre 194 y
204 por el redondeo de los límites de cubo. Esto **descarta también la explicación
que daba el capítulo**, que atribuía la pérdida al establecimiento simultáneo de
las conexiones con el broker.

## Lectura

A estas cargas la pérdida es un puñado de lecturas que aparece y desaparece entre
corridas por lo demás idénticas: 12, 2 y 9 sobre casi dieciséis mil. La razón de
que la celda de menor carga exhiba el porcentaje más alto de la matriz es
aritmética: el mismo puñado de lecturas dividido por el total más pequeño. Las 22
lecturas de la celda del 10 de agosto son 0,14 % sobre 15.885, pero serían 0,03 %
si hubieran ocurrido en la celda de 800 msg/s, que mueve cuatro veces más
mensajes.

Dicho de otro modo: en este rango el indicador no tiene resolución para sostener
una comparación entre celdas. Lo que sí sostiene, y con dos órdenes de magnitud
de margen, es el cumplimiento del umbral del 0,5 %.

**Condiciones.** Imagen `processor-0.9.0` con `--log-level WARNING` y
`--metrics-interval 10`; Mac al 75 % de idle; relojes NTP verificados. La campaña
del 10 de agosto se midió sobre `processor-0.6.0`, de modo que las cifras
absolutas no son directamente comparables, pero el orden de magnitud sí.
