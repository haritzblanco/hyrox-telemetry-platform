# El defecto del camino de escritura — 2026-09-09

Diagnóstico de la pérdida que aparece en las celdas de cuatro réplicas
estranguladas, y que resulta ser **el mismo defecto** que producía el desfase de
la métrica de persistencia documentado en `20260810_200637`.

## Qué se midió

Cuatro réplicas fijadas, límite de 700 m, 16 atletas × speedup 50 ≈ 800 msg/s,
imagen `processor-0.9.0` con `--log-level WARNING`. Contadores acumulados de cada
réplica al cierre, más el conteo en la base de datos.

| | Valor |
|---|---|
| Ofrecido (PUBACK del broker) | 63.990 |
| **Consumido por el procesado** | **63.990** |
| Confirmado por InfluxDB | 63.684 |
| **Errores de escritura** | **0** |
| En la base de datos | 63.684 |

Reparto por réplica de lo consumido y no confirmado: **144, 120, 26 y 16**.

## Qué queda descartado

**No es el broker.** Consumido = ofrecido, exactamente. Todo lo que el broker
confirmó al publicador llegó a una réplica.

**No es una excepción tragada.** `handle()` cuenta la lectura como consumida y
después llama a `writer.write()`; si esa llamada lanzara, el `except Exception` de
`_on_message` la registraría. Los logs de las cuatro réplicas no tienen ni una
sola línea de error.

**No es un problema de los datos.** La pérdida se reparte por igual entre los
dieciséis atletas (13 a 34 lecturas cada uno en la corrida equivalente) y a lo
largo de toda la corrida (785-800 lecturas persistidas por segundo, de principio
a fin), sin concentrarse en ningún atleta, fase ni instante.

## Qué es

Los puntos entran en `_write_api.write()` y **no llegan nunca a la base de datos,
sin producir error**: el cliente de InfluxDB los descarta en su tubería de
escritura por lotes. Que el conteo de la base coincida exactamente con el
`total_acked` del procesado —63.684 en ambos— confirma que no es un problema de
contabilidad: esos puntos no se escribieron.

## Los dos defectos son uno

Cada punto descartado deja su marca de tiempo atrapada en la cola FIFO que empareja
escrituras con confirmaciones, de modo que **inflaría todas las medidas de
persistencia posteriores de esa réplica** en `marcas atrapadas / tasa`. La
correlación es exacta:

| Réplica | Puntos perdidos | Persistencia p50 durante la corrida |
|---|---|---|
| 7dgsd | 144 | 373 → 920 ms (sube sin parar) |
| rnwrd | 120 | 394 → 795 ms (sube sin parar) |
| lkxvh | 26 | 359 → 423 ms (plana) |
| h25qz | 16 | 352 → 385 ms (plana) |

Las dos réplicas que pierden puntos son exactamente las dos cuya persistencia
medida crece durante la corrida, y las que no pierden la mantienen plana. Esto
**unifica** el desfase de ~20 lecturas encontrado en la campaña del 10 de agosto
con la pérdida de estas celdas: son el mismo fenómeno visto por dos indicadores.

## Alcance y gravedad

El efecto depende de la CPU disponible: con el límite de producción (1000 m) la
celda equivalente pierde 16 puntos, un 0,03 %; con 700 m pierde entre 306 y 396,
un 0,5-0,6 %. Estrangular la réplica multiplica el descarte por veinticinco, lo
que apunta a que la tubería de escritura del cliente pierde puntos cuando su hilo
de fondo se queda sin CPU.

**Causa raíz no identificada.** Está dentro de `influxdb_client` 1.50.0 sobre
`reactivex` 5.1.0, y localizarla exige instrumentar la tubería del propio cliente.
Lo que sí queda establecido es dónde NO está: ni en el broker, ni en el
serializador, ni en los datos, ni en una excepción del procesado.

## Qué hacer

1. **Hacerlo visible.** Exponer en la línea de métricas el tamaño de la cola de
   marcas pendientes: cuenta directamente los puntos descartados y convierte una
   pérdida invisible en una medida.
2. **No estrangular al escritor.** El descarte cae al 0,03 % con el límite de
   producción, lo que refuerza la decisión de no adoptar un límite ajustado.
3. Corregir la contabilidad de `on_ack`, que hoy arrastra el desfase para siempre.

## Consecuencia para el capítulo

El texto atribuye hoy toda la pérdida al descarte en la cola del broker. Es cierto
en las celdas saturadas, donde el consumido queda muy por debajo del ofrecido,
pero **no en las que no lo están**, donde ambos coinciden y la pérdida está en la
escritura. La afirmación hay que acotarla.
