# El lote propio en el clúster — 2026-09-10

Repite la corrida que destapó el defecto (`20260909_212221_diag_writer`) con la
imagen `processor-0.11.0`, la primera en la que el procesador agrupa y escribe
por su cuenta en vez de delegar el lote en el cliente de InfluxDB. Mismas
condiciones exactas: cuatro réplicas fijadas, límite de 700 m, 16 atletas ×
speedup 50 ≈ 800 msg/s, `--log-level WARNING`.

## Cero pérdida de extremo a extremo

| | 0.9.0 (lote del cliente) | **0.11.0 (lote propio)** |
|---|---|---|
| Ofrecido (PUBACK del broker) | 63.990 | 63.990 |
| Consumido por el procesado | 63.990 | 63.990 |
| Confirmado por InfluxDB | 63.684 | **63.990** |
| En la base de datos | 63.684 | **63.990** |
| Errores de escritura | 0 | 0 |
| **Perdidos** | **306 (0,48 %)** | **0** |

Las cuatro cifras coinciden: lo que el broker confirmó al publicador llegó a una
réplica, se escribió y está en la base. Ninguna réplica queda descuadrada
—15.997/15.997, 15.998/15.998, 15.997/15.997, 15.998/15.998—, frente al reparto
de 144, 120, 26 y 16 puntos perdidos de la corrida del defecto.

## La cola de escritura vuelve a cero

El suelo de `persist_pending` en las ventanas con carga es **0 en las cuatro
réplicas**. Con el lote del cliente, las réplicas afectadas nunca bajaban de su
suelo y este crecía conforme se acumulaban los descartes; ahora todas devuelven
la cola a cero entre lotes, que es lo que hace una cola de escritura sana.

El estimador de `analyze.py` da 5 marcas atrapadas en toda la corrida, un residuo
de cola real (a ~150 msg/s por réplica son unos 33 ms) y no una cuenta que se
quede fija.

## La latencia de persistencia deja de derivar

Mediana por ventana a lo largo de la corrida, muestreada cada nueve ventanas:

| Réplica | p50 (ms) | p95 final |
|---|---|---|
| cd225 | 311, 283, 285, 280, 273, 294, 277, 292, 289 | 513 |
| gjxn6 | 313, 274, 282, 285, 274, 290, 275, 295, 291 | 516 |
| p2cqp | 317, 285, 289, 284, 271, 295, 280, 295, 289 | 483 |
| vtfz6 | 316, 279, 287, 280, 274, 292, 279, 295, 290 | 525 |

Las cuatro planas en 271-317 ms. En la corrida del defecto, las dos réplicas que
perdían puntos subían sin parar —373 → 920 ms y 394 → 795 ms— mientras las
limpias se mantenían: era el desfase del emparejamiento FIFO, no una escritura
más lenta. Sin ese emparejamiento, la medida ya no deriva.

## El resto de la celda, sin cambios

Transporte p95 138 ms (peor réplica 151). Caudal confirmado 613 msg/s de media
sobre la ventana de medida. CPU 565 m de media y 603 m de pico por réplica contra
un límite de 700 m, así que el estrangulamiento seguía activo: la corrida es
comparable a la del defecto y no una versión más holgada del mismo escenario.
Memoria 142 Mi.

## Condiciones

Relojes sincronizados antes de la corrida (`NTPSynchronized=yes` en las tres VMs,
offset crudo 260-303 ms, dominado por el coste de `multipass exec`). El CronJob
de backup de InfluxDB había terminado antes de empezar. Ventana de experimento
abierta para la corrida (auto-sync de ArgoCD pausado en `root` y
`hyrox-platform`) y **cerrada al terminar**: las cinco aplicaciones quedaron
`Synced/Healthy`.

Producción vuelve a la `processor-0.10.0`, que es la que está comprometida en el
repositorio. La `0.11.0` está construida y publicada en el registro, y el chart
la apunta en el árbol de trabajo, pero **el cambio no está commiteado**: hasta
que lo esté, el clúster sigue con el lote del cliente.
