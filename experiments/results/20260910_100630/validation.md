# Escalado con réplicas estranguladas a 250 m — 2026-09-10

Matriz de escalabilidad rehecha sobre la imagen `processor-0.11.0`, la primera
con el lote de escritura propio. Sustituye a la del 7 de septiembre
(`20260907_203001`), que se midió con el escritor antiguo y cuyo límite de 150 m
ya no vale: con el escritor nuevo una réplica a 150 m absorbe 488 msg/s en vez de
68, así que a ese límite la matriz saldría casi plana.

Tres cuentas de réplicas por cuatro cargas, 250 m de CPU por réplica, speedup 50.

## Las tres mesetas

Pérdida de extremo a extremo (ofrecido según PUBACK del broker, persistido
contado en InfluxDB):

| Carga ofrecida | 1 réplica | 2 réplicas | 4 réplicas |
|---|---|---|---|
| 200 msg/s | 0,14 % | 0 % | 0 % |
| 400 msg/s | 24,12 % | 0 % | 0 % |
| 600 msg/s | 54,54 % | 3,28 % | 0 % |
| 800 msg/s | 60,61 % | 11,83 % | **0 %** |

A carga baja las tres configuraciones coinciden: el escalado no aporta nada
porque no hay nada que repartir. Conforme sube la carga, cada configuración se
planta en su techo, y los techos están separados. Con cuatro réplicas la
plataforma cubre el pico de diseño de 800 msg/s sin perder una sola lectura.

Lo mismo visto como caudal confirmado, que enseña dónde está el techo de cada
configuración en lugar de cuánto se descarta:

| Carga ofrecida | 1 réplica | 2 réplicas | 4 réplicas |
|---|---|---|---|
| 200 msg/s | 140,5 | 164,6 | 161,4 |
| 400 msg/s | 137,7 | 311,8 | 309,0 |
| 600 msg/s | 123,4 | 281,6 | 370,9 |
| 800 msg/s | 154,1 | 315,8 | 490,6 |

Una réplica no pasa de unos 140 msg/s por mucha carga que se le ofrezca; dos se
plantan cerca de 315; cuatro siguen subiendo hasta 490 en la ventana de medida.

## La latencia cuenta la misma historia, y más clara

Latencia de transporte p95, de la emisión en el simulador al consumo en el
procesador:

| Carga ofrecida | 1 réplica | 2 réplicas | 4 réplicas |
|---|---|---|---|
| 200 msg/s | 172 ms | 189 ms | 171 ms |
| 400 msg/s | 46,4 s | 199 ms | 172 ms |
| 600 msg/s | 64,4 s | 12,5 s | 197 ms |
| 800 msg/s | 56,5 s | 21,0 s | 217 ms |

Con cuatro réplicas el p95 se queda por debajo de 220 ms en todo el rango,
mientras que con una se va a casi un minuto en cuanto se pasa de su techo: los
mensajes esperan en la cola del broker a un suscriptor que no da abasto. Es la
diferencia entre un servicio que responde y uno que solo aparenta funcionar
porque los mensajes acaban llegando.

## El escritor no es el cuello en ninguna celda

Cero errores de escritura en las doce celdas. La latencia de persistencia se
mantiene entre 275 y 413 ms de media (p95 entre 507 y 687) con independencia de
la cuenta de réplicas y de la carga, incluso en las celdas que pierden el 60 %.
La pérdida es descarte en la cola del broker hacia un suscriptor lento, no
escritura fallida: lo que no se consume no llega nunca al escritor.

Consumo por réplica: con una réplica la cuota se agota (242-251 m de un límite de
250 m); con dos y con cuatro cada réplica se queda en 166-190 m, por debajo de su
cuota, porque ya no tiene que absorber más de lo que puede.

## Por qué 250 m, y una advertencia sobre la cuota

El límite se calibró antes de la matriz, sondeando una réplica con 800 msg/s
ofrecidos (`20260910_094811_calibracion`, `20260910_095357_calibracion` y las
corridas de 250 m y 500 m):

| Límite | Techo medido | Repeticiones |
|---|---|---|
| 50 m | 35 msg/s | |
| 75 m | 234, 246, 264 msg/s | estable |
| 100 m | 51, 44, 62 msg/s | estable |
| 150 m | 488 msg/s | |
| 250 m | 193, 191 msg/s | estable |
| 500 m | 853 msg/s | no llega a saturar (CPU 330 m, p95 155 ms) |

La relación entre cuota y caudal no es monótona, y las repeticiones descartan que
sea ruido: con 75 m una réplica rinde 246 msg/s gastando 80 m, y con 250 m rinde
191 gastando los 250. Al agotar la cuota el proceso queda detenido hasta el
periodo siguiente, y hay estados de los que no se recupera mientras dure la
carga. Se descartó que fueran reinicios de la sonda de vida: los pods no
acumularon ningún reinicio en toda la calibración.

De ahí que el límite haya que medirlo siempre antes de una matriz estrangulada, y
que no se pueda deducir del consumo medio. 250 m se eligió por dar un techo de
unos 190 msg/s, que separa las tres mesetas dentro del rango que el simulador
puede ofrecer, y por ser una cuarta parte del límite de producción, que es una
cifra defendible.

## Condiciones

Imagen `processor-0.11.0` en las doce celdas, `--log-level WARNING`, relojes
sincronizados. Ventana de experimento abierta para la calibración y la matriz, y
cerrada al terminar: las cinco aplicaciones de ArgoCD quedaron `Synced/Healthy` y
producción volvió a su límite de 1000 m.
