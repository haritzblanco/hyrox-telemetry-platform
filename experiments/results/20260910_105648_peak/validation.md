# Escalado automático al pico, arranque en frío — 2026-09-10

Corrida de escalado automático rehecha sobre `processor-0.11.0`, para acompañar
a la matriz de `20260910_100630`. Sustituye a la de `20260908_075355_peak`, que
se midió con el escritor antiguo y con 150 m por réplica.

Configuración de producción salvo el estrangulamiento: manda el ScaledObject de
KEDA (1 a 4 réplicas), 250 m por réplica igual que en la matriz, arranque desde
una sola réplica, 40 atletas × speedup 20 durante unos 230 s, unos 800 msg/s.

## KEDA recorre 1 a 4 réplicas en 84 segundos

| Instante | Réplicas listas |
|---|---|
| 0 s | 1 |
| 28 s | 2 |
| 73 s | 3 |
| 84 s | 4 |

El caudal confirmado sigue la escalera: parte de unos 150 msg/s, que es el techo
de una réplica con esta cuota, salta a unos 560 en cuanto entran la segunda y la
tercera, y alcanza los 800 ofrecidos hacia el segundo 85, cuando la cuarta ya
está lista. Desde ahí se sostiene en el ofrecido.

## Lo que cuesta arrancar en frío

| | Valor |
|---|---|
| Ofrecido (PUBACK del broker) | 159.787 |
| Persistido en InfluxDB | 134.126 |
| **Pérdida** | **25.661 (16,06 %)** |
| Errores de escritura | 0 |

La misma carga con cuatro réplicas fijadas desde el principio no pierde nada
(celda R4_N16 de la matriz). Los 25.661 mensajes se pierden en los 84 segundos
en que la plataforma todavía no tiene las réplicas que la carga exige: son
descarte en la cola del broker hacia unos suscriptores que no dan abasto, no
escritura fallida. **El coste del escalado reactivo es el tiempo que tarda en
reaccionar**, y con esta cuota ese tiempo se paga en mensajes.

Con el umbral del RNF-3 en el 0,5 %, esta corrida no lo cumple. Conviene leerlo
con su contexto: las réplicas van estranguladas a la cuarta parte del límite de
producción a propósito, para que el escalado tenga algo que rescatar. Con el
límite de producción una sola réplica absorbe 853 msg/s (ver la calibración de
500 m), de modo que al pico de diseño no hay nada que perder ni nada que
escalar.

## Latencia

Transporte p95 696 ms y persistencia p95 634 ms sobre la corrida completa, que
suman una cota de extremo a extremo de 1.329 ms, por debajo del umbral de 2 s
del RNF-2. La media de transporte, en cambio, es de 17,3 s, y la peor ventana
llega a 82 s: la distribución tiene una cola larguísima que la media recoge y el
p95 no.

Esa cola tiene dos tramos y conviene distinguirlos. El primero, entre los
segundos 20 y 60, es el atasco mientras faltan réplicas. El segundo, a partir
del segundo 200, es el broker vaciando la cola que acumuló al principio: los
mensajes que entrega entonces son viejos, y su latencia de transporte mide la
espera en la cola, no la lentitud del procesado. La persistencia se mantiene
plana por debajo de 1 s durante toda la corrida, incluidos ambos tramos, lo que
confirma que el escritor no interviene en ninguno.

## Condiciones

Imagen `processor-0.11.0`, `--log-level WARNING`, relojes sincronizados. Ventana
de experimento abierta para la corrida y cerrada al terminar: las cinco
aplicaciones de ArgoCD quedaron `Synced/Healthy` y producción volvió a su límite
de 1000 m.
