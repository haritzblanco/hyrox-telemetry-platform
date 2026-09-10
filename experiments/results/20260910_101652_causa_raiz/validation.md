# Causa raíz del descarte silencioso del cliente — 2026-09-10

Cierra el diagnóstico de `20260909_212221_diag_writer`, que dejó establecido
dónde **no** estaba la pérdida (ni broker, ni serializador, ni datos, ni
excepción tragada) pero no dónde estaba. Es una carrera entre hilos en el
operador de ventanas del cliente de InfluxDB, y se reproduce en local, sin
clúster, con `experiments/load-tests/bench_write_api.py`.

## El defecto

La escritura por lotes del cliente pasa por
`reactivex.operators.window_with_time_or_count`, que trocea el flujo de puntos
en ventanas por tamaño de lote **o** por tiempo de vaciado. El operador guarda la
ventana en curso en una sola variable `s`, un `Subject`, y la sustituye por otra
cada vez que la ventana se cierra. Esa sustitución ocurre desde dos hilos
distintos y **sin ningún cerrojo entre ellos**:

- Por cuenta, desde el hilo del productor (nuestro callback de MQTT), dentro del
  mismo `on_next` que acaba de encolar el punto.
- Por tiempo, desde el hilo del temporizador (`flush_interval`, 500 ms), que
  ejecuta `s.on_completed(); s = Subject(); observer.on_next(nueva_ventana)`.

El productor lee `s` y llama a `s.on_next(punto)`. Si entre esas dos operaciones
el temporizador se cuela, el punto cae en un `Subject` que ya no sirve, y un
`Subject` **descarta en silencio**: no lanza, no registra, no llama a ningún
callback de error. De ahí que la pérdida fuese invisible para todos los
contadores del procesador salvo para la cola de marcas pendientes.

Hay dos variantes de la carrera, y la sonda del banco las distingue:

| Vía | Qué pasa |
|---|---|
| **Ventana ya cerrada** | El productor escribe en la ventana anterior después de que el temporizador la haya completado. |
| **Ventana sin suscriptor** | El productor escribe en la ventana nueva antes de que el resto de la tubería haya llegado a suscribirse a ella. |

## La prueba

Banco local: el `WriteApi` real en modo lote con el POST HTTP sustituido por un
contador, 12.000 puntos a 800 msg/s (el pico de diseño), lote de 500 y vaciado
cada 500 ms, que son los ajustes de producción. Los «quemadores» son hilos que
compiten por el GIL e imitan la falta de CPU de una réplica estrangulada;
`switch` es `sys.setswitchinterval`, que ensancha la ventana de carrera.

| Corrida | Perdidos | A ventana cerrada | A ventana sin suscriptor |
|---|---|---|---|
| **A.** sin competencia, switch por defecto | 5 (0,042 %) | 5 | 0 |
| **B.** 4 quemadores, switch por defecto | 447 (3,7 %) | 447 | 0 |
| **C.** 4 quemadores, switch 1 µs | 1.110 (9,3 %) | 786 | 325 |
| **D.** como C, **sin temporizador** (`flush_interval` 600 s) | **0** | 0 | 0 |
| **E.** como C, **solo temporizador** (`batch_size` 10⁷) | 1.119 (9,3 %) | 803 | 314 |

Salida íntegra en `bateria.txt`.

**Las dos vías suman los puntos perdidos.** 786 + 325 = 1.111 frente a 1.110
medidos; 803 + 314 = 1.117 frente a 1.119. El margen de una o dos lecturas es de
la propia sonda, que comprueba el estado del `Subject` un instante antes de la
entrega. Ningún otro sumidero interviene: la fuga es entera de la carrera.

**D es la corrida decisiva.** Con el vaciado por tiempo desactivado, todas las
ventanas se cierran desde el hilo del productor, no hay dos hilos tocando `s` y
la pérdida cae a **cero exacto** en las mismas condiciones de CPU que producen un
9,3 %. E confirma el recíproco: con el cierre por cuenta desactivado, la pérdida
es la misma que con ambos activos. El temporizador es necesario y suficiente.

**Y explica el gradiente que se midió en el clúster.** A y B son la misma
configuración salvo la competencia por la CPU: 0,042 % contra 3,7 %. Es el mismo
efecto que llevaba la celda de cuatro réplicas de 0,03 % con el límite de
producción a 0,5-0,6 % con 700 m. La réplica estrangulada no pierde puntos porque
le falte capacidad de escritura, sino porque el hilo del temporizador y el del
callback de MQTT se interrumpen mutuamente más a menudo.

## Qué queda descartado con esto

No es un fallo de la red ni de InfluxDB: el banco no habla con InfluxDB. No es el
tamaño del lote, ni la serialización, ni el reintento: el POST está sustituido
por un contador que nunca falla. No es específico de nuestro código: el banco no
usa el procesador, solo el cliente.

## Alcance

`influxdb_client` 1.50.0. El banco lo reproduce sobre `reactivex` 4.1.0, que es
lo que hay en el entorno local, mientras que la imagen del procesador instala
`reactivex` 5.1.0. **El operador es el mismo en las dos**: se comprobó dentro de
la imagen construida que `window_with_time_or_count` de la 5.1.0 sustituye la
ventana sin ningún cerrojo, exactamente igual que la 4.1.0. El defecto está en
`reactivex`, así que afecta a cualquier usuario del modo de escritura por lotes
con un productor en otro hilo, que es el uso recomendado por el propio cliente.
El `pyproject.toml` del procesador no fija versiones (`influxdb-client>=1.43.0`),
de modo que la pareja instalada depende de la fecha de construcción.

## La corrección: lote propio

Se descartaron dos salidas menores antes de escribir la definitiva. Desactivar el
vaciado por tiempo (la corrida D) da pérdida cero, pero deja el último lote
parcial sin escribir hasta que se complete la cuenta —lo que a caudal bajo
dispara la latencia de persistencia— y cuelga el cierre del cliente esperando al
temporizador pendiente. No estrangular al escritor, que era la conclusión del 9
de septiembre, no arregla nada: solo mantiene el defecto en el 0,03 %.

**`InfluxWriter` agrupa y escribe por su cuenta.** `write()` sigue sin bloquear
el callback de MQTT: serializa la lectura y la encola en una `queue.Queue` con su
marca de encolado. Un hilo propio cierra el lote por tamaño (500) o por intervalo
(500 ms), que son los mismos ajustes que tenía el cliente, y lo escribe en modo
**síncrono**, con reintento de espera creciente. El cliente de InfluxDB deja de
gestionar ventanas, y con ellas desaparece la carrera.

Se gana además exactitud en la medida: cada punto lleva su propia marca, así que
la latencia de persistencia sale de una resta directa al confirmarse el lote. El
emparejamiento FIFO que se desalineaba para siempre (`PersistenceTracker`) ya no
existe. Y lo que no cabe en la cola —50.000 puntos, un minuto de retraso al pico
de diseño— se descarta **contado y registrado**, nunca en silencio, que era
justamente el vicio del que se venía.

`persist_pending` cambia de significado en consecuencia: sigue siendo el suelo de
la cola, pero ahora mide cola de escritura real, no marcas atrapadas. Para leer
las campañas anteriores a esta imagen sigue valiendo `marcas_atrapadas` de
`analyze.py`.

### Verificación

El escritor nuevo, en las mismas condiciones que la corrida C y en la misma
sesión, contra el mismo POST sustituido por un contador (`comparacion_lote_propio.txt`):

| | Perdidos | Persistencia p50 / p95 |
|---|---|---|
| Lote del cliente | 1.148 (9,567 %) | — |
| **Lote propio** | **0 (0,000 %)** | 267 / 493 ms |
| **Lote propio, 8 quemadores** | **0 (0,000 %)** | 274 / 511 ms |

12.000 confirmados de 12.000, cero errores, con el doble de competencia por la
CPU de la que bastaba para perder un 9,6 %. Los 267 ms de mediana son los que
cabe esperar de una ventana de vaciado de 500 ms y coinciden con los 272-284 que
medían las réplicas limpias en el clúster.

## Qué queda por hacer

La imagen `processor-0.11.0` está construida para linux/amd64 y publicada en el
registro, y el chart apunta ya a ella. Falta que el cambio llegue al repositorio
para que ArgoCD lo despliegue; hasta entonces el clúster sigue con el lote del
cliente. Después conviene repetir la celda de cuatro réplicas estranguladas a
700 m, que es donde el defecto se veía sin ambigüedad, y comprobar que el suelo
de `persist_pending` ya solo refleja cola de escritura.

## Consecuencia para el capítulo

La afirmación del capítulo sobre la pérdida se puede cerrar ahora del todo: en
las celdas saturadas es descarte en la cola del broker, y en las que no lo están
es esta carrera dentro del cliente de InfluxDB, cuantificada por el suelo de
`persist_pending` y reproducida fuera del clúster.
