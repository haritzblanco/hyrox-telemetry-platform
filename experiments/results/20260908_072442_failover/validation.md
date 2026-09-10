# Caída de una réplica en el pico de diseño — 2026-09-08

Mide qué le cuesta al servicio perder una réplica de golpe mientras atiende el
pico de diseño. Responde a la pregunta que dejan abiertas los apartados de
escalabilidad: si una réplica basta para el caudal, la segunda solo se justifica
por disponibilidad, y eso hay que medirlo en vez de afirmarlo.

Arnés: `experiments/load-tests/run_failover.sh`.

**Configuración.** Dos réplicas congeladas con `paused-replicas`, límites de
producción (mil milicores por contenedor), carga de 40 atletas × speedup 20 ≈
800 msg/s durante 230 s. A los 91 s se elimina una réplica con
`--grace-period=0 --force`, es decir, sin terminación ordenada: con un apagado
limpio el pod se despide del broker y la corrida mediría un relevo, que es el
caso fácil. Las dos réplicas caen una en cada nodo de cómputo por la
anti-afinidad; la eliminada es la de `hyrox-worker` y la superviviente la de
`hyrox-worker2`, que comparte nodo con el broker.

A diferencia del resto del arnés, la corrida sigue los logs de cada pod desde el
principio: los de la réplica eliminada desaparecen con ella y no se pueden leer
al final.

## Resultado

| Magnitud | Valor |
|---|---|
| Ofrecido (confirmado por el broker) | 159.787 |
| Persistido | 159.297 |
| Pérdida | 490 lecturas, **0,31 %** |
| Baja de la réplica | t = 91 s |
| Réplica de reemplazo consumiendo | t ≈ 108 s (17 s después) |
| Latencia de transporte p95, antes de la baja | 210-840 ms |
| Latencia de transporte p95, máximo tras la baja | 6,1 s (t = 109 s) |

Cronología por réplica (caudal confirmado / transporte p95 por ventana de 10 s):

- `jdjpn` (eliminada, hyrox-worker): 385-410 msg/s con 213-516 ms hasta los 91 s.
- `lpr95` (superviviente, hyrox-worker2): 377-423 msg/s con 209-837 ms antes de
  la baja; tras ella el caudal se mantiene en torno a 400 msg/s y la latencia
  sube a 3,3 s, alcanza 6,1 s y va bajando hasta 1,9 s al final de la corrida.
- `8gl8s` (reemplazo, hyrox-worker): entra a los 108 s y trabaja desde el primer
  instante en régimen normal, 382-408 msg/s con 236-472 ms.

## Lectura

**El servicio no se interrumpe.** En ninguna ventana el caudal confirmado cae a
cero ni se produce un corte: la suscripción compartida reencamina hacia la
réplica que queda en cuanto el broker da por muerta la sesión de la otra.

**El coste de una caída abrupta es de 490 lecturas, un 0,31 %,** por debajo del
umbral del 0,5 % del RNF-3. Son los mensajes que el broker tenía encolados o en
vuelo hacia la réplica eliminada: como el cliente usa sesión limpia, esa cola se
descarta al desconectarse y no se reentrega. Es el precio de no tener
terminación ordenada, y acota lo que cuesta el peor caso.

**Lo que sí se degrada es la latencia, y de forma transitoria.** Durante los 17
segundos en que la superviviente estuvo sola no llegó a absorber los 800 msg/s
del pico: se mantuvo en torno a los 400 que ya venía atendiendo, y el resto se
acumuló en la cola del broker. Esa cola es la que dispara el percentil 95 a 6,1
segundos, por encima del umbral de 2 s del RNF-2, y la que la pareja de réplicas
tarda el resto de la corrida en drenar. La réplica de reemplazo, en cambio,
trabaja en régimen normal desde su primera ventana.

**Consecuencia para el dimensionado.** Dos réplicas dan redundancia efectiva en
el sentido que importa, que es que el servicio sigue en pie y la pérdida se
mantiene dentro del umbral, pero no la dan gratis: mientras el planificador
repone la instancia, el sistema atraviesa un tramo de latencia por encima del
umbral. Conviene además señalar que con suscripción compartida no existe la
figura del suplente en reposo: las dos réplicas consumen a la vez, cada una la
mitad del caudal, y la redundancia se manifiesta como capacidad de absorber el
total, no como una instancia ociosa esperando a que la otra falle.

## Condiciones y comparabilidad

**Nivel de registro.** La primera ejecución de esta corrida
(`20260908_071810_failover`, **no citable**) se hizo con el Deployment de
producción tal cual, que registra a nivel INFO una línea por cada 50 lecturas
persistidas: unas 16 líneas por segundo y réplica al pico. El sobrecoste elevó
el consumo a 441-652 m por réplica y la pérdida a un 0,54 %. Repetida con
`--log-level WARNING`, que es el que usan todas las campañas anteriores, la
pérdida baja al 0,31 %. **Es un dato sobre la configuración de producción, no
solo sobre el experimento:** en producción el procesador paga ese registro.

**Versión de la imagen.** Esta corrida usa la imagen de producción
`processor-0.9.0`, mientras que las campañas de junio a agosto se midieron sobre
`processor-0.6.0` a través de `processor-exp.yaml`. El consumo por réplica de
esta corrida (438-620 m de media para unos 400 msg/s) queda por encima de los
269 m que la matriz del 10 de agosto midió en régimen para el mismo caudal por réplica. La
diferencia no está aislada aquí —intervienen también el estado del anfitrión y
la co-ubicación con el broker—, de modo que no se puede atribuir con estos datos
a la versión, pero conviene tenerla presente antes de mezclar cifras absolutas
de una campaña y otra.
