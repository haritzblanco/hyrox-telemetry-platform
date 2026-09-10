# El lote propio con 750 m por réplica — 2026-09-10

Segundo punto de la validación de `processor-0.11.0` en el clúster, con el
estrangulamiento algo más flojo que en `20260910_084824`. Misma celda: cuatro
réplicas fijadas, 16 atletas × speedup 50 ≈ 800 msg/s, `--log-level WARNING`.

## Cero pérdida, otra vez

| | 700 m | **750 m** |
|---|---|---|
| Ofrecido (PUBACK del broker) | 63.990 | 63.990 |
| Consumido | 63.990 | 63.990 |
| Confirmado | 63.990 | 63.990 |
| En la base de datos | 63.990 | **63.990** |
| Errores de escritura | 0 | 0 |
| **Perdidos** | **0** | **0** |

Reparto por réplica: 15.998/15.998, 15.998/15.998, 15.997/15.997 y
15.997/15.997. Ninguna queda descuadrada.

## La cola vacía en las cuatro

El suelo de `persist_pending` llega a cero en algún momento de la corrida en
todas las réplicas, así que ninguna arrastra un residuo permanente. Entre lote y
lote ronda los 4 puntos, que a ~150 msg/s por réplica son unas decenas de
milisegundos de retraso transitorio.

El estimador de `analyze.py` da 20 marcas atrapadas en la corrida frente a las 5
de la de 700 m. Con el lote propio esa cifra ya no puede ser desfase del
instrumento: es cola de escritura, y su variación entre corridas mide lo ocupado
que estaba el anfitrión, no un defecto.

## La latencia sube un poco, pero no deriva

Mediana de persistencia por ventana, muestreada cada nueve ventanas:

| Réplica | p50 (ms) |
|---|---|
| fvngj | 362, 299, 296, 313, 324, 325, 292, 337, 319 |
| l8kgl | 316, 293, 314, 329, 341, 319, 287, 387, 318 |
| z2mlj | 349, 285, 293, 333, 321, 343, 298, 368, 334 |
| zs7dr | 344, 286, 314, 334, 315, 330, 279, 347, 373 |

Las cuatro se mueven en 279-387 ms, algo por encima de los 271-317 de la corrida
de 700 m y con más dispersión, pero ninguna con tendencia creciente. La
diferencia va con el anfitrión, no con el límite: esta corrida consumió más CPU
(636 m de media y 742 m de pico por réplica, contra 565 y 603 con el límite de
700 m) y su cola de escritura fue algo mayor. El p95 agregado sube de 517 a
554 ms y el de la peor réplica de 580 a 844 ms.

En sentido contrario, el transporte salió mejor: p95 122 ms frente a 138.

## Qué añade este punto

Que el resultado no dependía de un límite concreto. Con 700 m y con 750 m, en
corridas separadas y con el anfitrión en distinto estado de ocupación, la pérdida
es cero y ninguna réplica acumula marcas. Con el lote del cliente, la misma celda
a 700 m perdía 306 puntos repartidos de forma desigual entre réplicas
(`20260909_212221_diag_writer`).

## Condiciones

Relojes sincronizados antes de la corrida. Ventana de experimento abierta para
las dos corridas del día y cerrada al terminar: las cinco aplicaciones de ArgoCD
quedaron `Synced/Healthy`. Producción vuelve a la `processor-0.10.0`, porque el
salto del chart a la 0.11.0 está commiteado pero sin subir al remoto, que es de
donde reconcilia ArgoCD.
