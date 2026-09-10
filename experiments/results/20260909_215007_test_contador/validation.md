# Validación del contador de marcas pendientes — 2026-09-09

Primera corrida con la imagen `processor-0.10.0`, que publica el tamaño de la
cola de marcas pendientes. Cuatro réplicas estranguladas a 700 m, 16 atletas ×
speedup 50 ≈ 800 msg/s, que es el escenario donde se manifiesta el descarte
silencioso del cliente de InfluxDB.

## El contador acierta a la lectura

| Réplica | Consumido | Confirmado | **Perdidos** | **Cola al final** |
|---|---|---|---|---|
| j9ws9 | 15.997 | 15.988 | **9** | **9** |
| rct4q | 15.998 | 15.993 | **5** | **5** |
| slm29 | 15.997 | 15.997 | **0** | **0** |
| txr6q | 15.998 | 15.998 | **0** | **0** |

Coincidencia exacta en las cuatro. Una vez drenada la corrida no queda ninguna
marca legítima en vuelo, así que lo que sobra en la cola son exactamente los
puntos que el cliente descartó.

## Y lo delata mientras la corrida está en curso

El suelo de la cola por ventana de diez segundos separa a las réplicas sanas de
las afectadas sin esperar al final:

| Réplica | Suelo por ventana | Persistencia p50 |
|---|---|---|
| j9ws9 (9 perdidos) | 4, 4, 4, 4, 5, 4, 5, 4, 5 | 292 → 303 ms |
| rct4q (5 perdidos) | 2, 2, 2, 4, 4, 7, 8, 5, 5 | 286 → 308 ms |
| slm29 (0) | 3, 0, 2, 3, 1, 0, 1, 2, 0 | 274-284 ms, plana |
| txr6q (0) | 0, 1, 0, 1, 1, 0, 1, 3, 0 | 272-288 ms, plana |

Las sanas devuelven la cola a cero varias veces; las afectadas nunca bajan de
su suelo, y ese suelo crece conforme se acumulan los descartes (rct4q: 2 → 8).

**El desvío de latencia cuadra con la aritmética del modelo.** Las dos réplicas
con marcas atrapadas miden 292-308 ms de persistencia frente a los 272-284 de
las limpias: unos 25 ms de diferencia, que a 200 msg/s por réplica son
justamente las 5 marcas del suelo. Queda así confirmada la relación
`exceso = marcas atrapadas / tasa` sobre la que se apoyaba todo el diagnóstico.

## Nota sobre la magnitud

Esta corrida perdió 14 puntos en total (0,02 %) frente a los 306-396 de las
corridas del diagnóstico, con el mismo límite de 700 m. El descarte varía mucho
entre corridas según lo ocupado que esté el anfitrión (aquí, 82 % de idle). Que
la cifra sea pequeña no resta valor a la prueba: el contador la siguió con
precisión de una lectura.
