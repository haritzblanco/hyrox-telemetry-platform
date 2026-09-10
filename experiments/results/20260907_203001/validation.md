# Escalado bajo saturación provocada — 2026-09-07

Mide qué aporta cada réplica cuando el procesado es el elemento limitante, que
es lo que la matriz de la configuración de producción (`20260810_200637`) no
puede mostrar.

**Motivo de la campaña.** Con el límite de producción, mil milicores por
réplica, una sola absorbe el pico de diseño completo, de modo que las tres
configuraciones de la matriz dan la misma curva superpuesta a la diagonal
ideal. La consecuencia es que aquella matriz demuestra que la plataforma cumple
el requisito, pero no demuestra que el escalado horizontal sirva para algo: no
hay ninguna carga, dentro del rango medible, en la que una réplica se quede
corta y varias lo resuelvan. Subir la carga por encima de 800 msg/s tampoco
vale, porque a partir de ahí el que se satura es el equipo que genera el
tráfico y la medida deja de ser de la plataforma.

**Método.** En lugar de subir la carga hasta el techo de una réplica, se baja el
techo de la réplica hasta la carga. Cada réplica se estrangula a 150 milicores
mediante `CPU_LIMIT` en `run_matrix.sh`, que fija petición y límite al mismo
valor. La variable manipulada sigue siendo el número de réplicas y el cuello de
botella pasa a estar dentro del clúster, no en el anfitrión, de modo que el
resultado ya no depende de la capacidad del portátil.

Matriz: 1, 2 y 4 réplicas × 50, 100, 200, 400, 600 y 800 msg/s, 18 celdas de
unos 92 s de carga. Réplicas congeladas con `autoscaling.keda.sh/paused-replicas`.
Relojes NTP verificados (offsets crudos de 253 a 261 ms) y Mac al 82 % de idle
al arrancar la tanda.

**Configuración medida, para poder reproducirla.** La tanda se ejecutó sobre el
Deployment de producción parcheado con `--metrics-interval 10`, es decir, con la
imagen `processor-0.9.0` y el nivel de registro INFO que trae producción. Las
campañas de junio a agosto usaban `processor-exp.yaml`, con la imagen 0.6.0 y
`--log-level WARNING`. La diferencia no es inocua: a nivel INFO el procesador
emite una línea por cada 50 lecturas persistidas, unas 16 por segundo y réplica
al pico, y ese sobrecoste entra en las capacidades absolutas que mide esta
matriz. Como las 18 celdas comparten configuración, la comparación entre
configuraciones de réplicas es válida; lo que no se puede es llevar las cifras
absolutas de caudal por réplica a las campañas anteriores.

## Dos correcciones al arnés que esta campaña exigió

**Espera de drenaje.** El margen fijo de tres segundos entre el final de la
carga y el conteo en InfluxDB solo vale mientras la plataforma no se satura. Una
celda saturada deja hasta diez mil mensajes encolados por suscriptor, y contar
en ese instante apunta como pérdida lo que solo está esperando; además la celda
siguiente hereda el atasco de la anterior, que fue lo que arruinó el primer
intento (`20260907_201016`, **no citable**). `run_matrix.sh` espera ahora a que
el consumo agregado caiga a cero en dos sondeos seguidos antes de contar.

**El límite no se puede predecir del consumo medio.** Una réplica que a 800
msg/s promedia 293 m no absorbe 800 msg/s con un límite de 300 m. El límite es
una cuota por periodo, no un promedio: cada vez que el proceso la agota queda
detenido hasta el periodo siguiente. La relación entre límite y techo hay que
medirla, y para eso está `calibrate_limit.sh`.

## El hallazgo que explica la calibración: la cuota encarece y además desestabiliza

La calibración dio, a primera vista, resultados contradictorios: 300 m absorbían
190 msg/s, 500 m absorbían los 800 ofrecidos con solo 365 m de consumo, y 700 m
se quedaban en 490 msg/s quemando la cuota entera. La contradicción se resuelve
al ver que el coste por mensaje no es constante:

La cuota encarece cada mensaje aunque la réplica vaya al día, porque cada vez
que el proceso la agota queda detenido hasta el periodo siguiente y eso rompe el
agrupamiento de escrituras:

| Configuración | Caudal sostenido | CPU en régimen |
|---|---|---|
| Sin estrangular (límite 1000 m) | 200 msg/s | 103 m |
| Sin estrangular (límite 1000 m) | 800 msg/s | 333 m |
| Límite 150 m | 50 msg/s | 120 m |
| Límite 150 m | 100 msg/s | 150 m (pegada a la cuota) |
| Límite 300 m | 190 msg/s | 300 m (pegada a la cuota) |
| Límite 500 m | 806 msg/s | 365 m (no llega a la cuota) |

Sin estrangular el coste marginal es de unos 0,38 mCPU por msg/s más unos 37 m
fijos. Con la cuota en 150 m, sostener 50 msg/s cuesta 120 m, más del doble de
lo que costaría sin estrangular, y el techo se queda en torno a 100 msg/s. La
segunda parte del comportamiento es la que importa para leer la matriz: por
encima de ese techo la réplica no sirve su capacidad y encola el resto, sino que
**se descuelga**, y el caudal absorbido cae a unos 67 msg/s mientras sigue
quemando la misma cuota. El paso a ese régimen no se revierte mientras dure la
carga, y depende de transitorios: el mismo límite de 150 m absorbe 200 msg/s en
una corrida en frío con diez clientes lentos y se hunde a 67 con cuatro rápidos.

## Resultados

Caudal en régimen, pérdida, latencia de transporte p95 y consumo por réplica.

| Réplicas | Carga | Caudal | Pérdida | Transporte p95 | CPU/réplica |
|---|---|---|---|---|---|
| 1 | 50 | 50 | 0,03 % | 0,2 s | 104 m |
| 1 | 100 | 98 | 0,20 % | 0,6 s | 129 m |
| 1 | 200 | 66 | 0,16 % | 78,6 s | 135 m |
| 1 | 400 | 68 | 48,17 % | 108,2 s | 132 m |
| 1 | 600 | 73 | 63,47 % | 110,4 s | 137 m |
| 1 | 800 | 67 | 73,49 % | 122,7 s | 138 m |
| 2 | 50 | 50 | 0,05 % | 0,1 s | 74 m |
| 2 | 100 | 100 | 0,13 % | 0,2 s | 117 m |
| 2 | 200 | 154 | 0,74 % | 10,6 s | 131 m |
| 2 | 400 | 140 | 0,63 % | 74,8 s | 132 m |
| 2 | 600 | 150 | 30,99 % | 91,5 s | 135 m |
| 2 | 800 | 155 | 45,99 % | 95,7 s | 126 m |
| 4 | 50 | 50 | 0,05 % | 0,1 s | 51 m |
| 4 | 100 | 99 | 0,24 % | 0,2 s | 81 m |
| 4 | 200 | 198 | 0,59 % | 0,2 s | 111 m |
| 4 | 400 | 383 | 1,29 % | 1,7 s | 136 m |
| 4 | 600 | 344 | 1,15 % | 32,3 s | 115 m |
| 4 | 800 | 338 | 1,10 % | 57,8 s | 112 m |

## Lectura

**Cada configuración tiene su codo y el codo se desplaza con el número de
réplicas.** Las tres curvas siguen la diagonal ideal mientras la carga cabe en
la capacidad disponible y se aplanan en cuanto la supera: una réplica se
despega entre 100 y 200 msg/s, dos entre 100 y 200 con meseta al doble, y cuatro
aguantan la diagonal hasta 400. Es la familia de curvas de saturación que la
matriz de producción no podía dar.

**El caudal escala con las réplicas.** Las mesetas quedan en 68, 150 y 341
msg/s, es decir, la capacidad agregada crece al menos proporcionalmente al
número de réplicas. El reparto por réplica sube ligeramente con la cuenta (68,
75 y 85 msg/s), lo que es coherente con el hallazgo anterior: cuanto menor es el
exceso que le toca a cada una, menos la castiga el régimen de descuelgue.

**La lectura más directa está en una sola fila.** A 200 msg/s ofrecidos, la
misma carga y la misma cuota por réplica, la latencia de transporte p95 pasa de
78,6 segundos con una réplica a 10,6 con dos y a 0,2 con cuatro. Tres órdenes de
magnitud que solo dependen del número de instancias.

**La pérdida no es aquí el mejor indicador, y conviene decirlo.** Con QoS 1, cola
acotada en diez mil mensajes por suscriptor y espera de drenaje, un sistema
saturado convierte el exceso en latencia antes que en descarte: solo se pierde
lo que desborda la cola mientras dura la carga. A eso se añade que el generador
se autolimita, porque el broker deja de confirmar publicaciones cuando la cola
está llena, de modo que la tasa realmente ofrecida en las celdas saturadas es
menor que la nominal. Las cifras de pérdida de la tabla son, por tanto, una cota
inferior del daño; el indicador que describe la saturación sin ambigüedad es la
pareja caudal-latencia.

## Alcance

Esta campaña **no mide la capacidad de la plataforma**, que es la que da la
matriz de producción: mide la ley de escalado con la capacidad por réplica
reducida a propósito para que quepa en el rango que el generador puede servir
sin saturarse. Las cifras absolutas de caudal (68, 150, 341 msg/s) son
consecuencia del límite de 150 m y no dicen nada sobre lo que la plataforma
sostiene en producción, donde una sola réplica con mil milicores absorbe los 800
msg/s del pico de diseño con 333 m y 712 ms de extremo a extremo.

## Corridas no citables de esta sesión

- `20260907_201016` — primer intento con 125 m. Sin espera de drenaje, cada
  celda heredaba el atasco de la anterior y la latencia crecía monótonamente de
  celda en celda (4 s → 159 s). Medía el arnés, no la plataforma.
- `20260907_213611_peak` — corrida con KEDA y réplicas estranguladas a 300 msg/s
  con `speedup 20`. Con una réplica descolgada desde el principio, el generador
  quedó bloqueado por el control de flujo del broker y una corrida de 230 s se
  estiró más de dos horas. Con réplicas estranguladas hay que usar perfiles
  cortos (`speedup 50`) y cargas que la topología final pueda absorber.

Figuras en `docs/figuras/cap10/estrangulado/`, generadas con
`build_figures.py --matrix experiments/results/20260907_203001 --out docs/figuras/cap10/estrangulado`.
