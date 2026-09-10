# Escalado automático con réplicas estranguladas — 2026-09-08

Cronología de una corrida en frío con el autoescalador al mando y las réplicas
estranguladas a 150 milicores, es decir, la misma configuración de la matriz
`20260907_203001` pero dejando decidir a KEDA en lugar de congelar la cuenta.

Complementa la corrida en frío de la configuración de producción
(`20260809_192126_target300_cold3`): allí una réplica sobra para el pico, de modo
que el escalado no tiene nada que corregir; aquí la réplica inicial está al
límite de lo que puede atender y se ve qué hace el autoescalador al respecto.

Arnés: `run_peak.sh` con `CPU_LIMIT=150m`. Carga de 10 atletas × speedup 20 ≈ 200
msg/s durante 217 s. Arranque desde una sola réplica. Imagen `processor-0.9.0`
con el registro a nivel INFO de producción, igual que la matriz que complementa.

## Resultado

| Magnitud | Valor |
|---|---|
| Ofrecido / persistido | 40.074 / 40.006 |
| Pérdida | 68 lecturas, **0,17 %** |
| Segunda réplica lista | t = 44 s |
| Tercera / cuarta | t = 117 s / t = 212 s |
| Utilización de CPU que ve el autoescalador | 93-100 % de la petición |
| Transporte p95 con una réplica | 780 → 1.231 → 1.528 → **1.819 ms** |
| Transporte p95 tras entrar la segunda | **232 ms**, y entre 130 y 210 ms el resto |

## Lectura

**Lo que el escalado rescata aquí es la latencia, no el caudal.** La réplica
inicial absorbe prácticamente todo lo que se le ofrece (192-201 msg/s frente a
200), así que la curva de caudal se mantiene sobre la carga ofrecida de principio
a fin. El déficit es pequeño pero constante, y un déficit constante no se ve en
el caudal: se acumula en la cola del broker y se manifiesta como una latencia que
crece de forma lineal, unos 350 ms por cada ventana de diez segundos. En cuatro
ventanas había pasado de 780 a 1.819 ms, camino del umbral de 2 s.

**La corrección es inmediata y de una sola ventana.** Cuando la segunda réplica
queda lista a los 44 s, el reparto pasa a ser de unos 100 msg/s por instancia,
la cola drena y el percentil 95 cae a 232 ms, donde se queda el resto de la
corrida pese a que la carga no ha bajado. Las réplicas tercera y cuarta llegan
después y solo añaden margen: el reparto baja a 65-68 msg/s por instancia sin que
la latencia mejore ya de forma apreciable.

**El disparador que actúa es el de CPU**, como en la corrida de producción: con
petición y límite igualados a 150 m, la réplica al límite marca entre el 93 y el
100 % de utilización, muy por encima del umbral del 75 %. El disparador de caudal
del broker no interviene, porque 200 msg/s quedan lejos de su objetivo de 400 por
réplica.

## Un matiz que conviene registrar

La misma configuración nominal —una réplica, 150 m, 200 msg/s— **se hundió** en
la celda `R1_N4` de la matriz (66 msg/s absorbidos, 78,6 s de latencia) y **no se
hunde aquí**. La diferencia está en el perfil de la carga: allí eran 4 clientes a
50 msg/s cada uno con speedup 50, aquí 10 clientes a 20 msg/s con speedup 20.

Esto refuerza el hallazgo de la matriz sobre los dos regímenes de coste por
mensaje: el paso al régimen degradado no lo dispara un umbral limpio de
capacidad, sino un transitorio que deje a la réplica descolgada. La consecuencia
operativa es la que justifica el disparador de CPU: su valor está en añadir
capacidad **antes** de que la réplica se descuelgue, porque una vez descolgada el
coste por mensaje se multiplica y hace falta mucha más capacidad para volver
atrás que la que habría bastado para evitarlo.

## Corrida anterior descartada

`20260907_213611_peak`, mismo experimento con 300 msg/s. Con la réplica única
descolgada desde el principio, el control de flujo del broker bloqueó al
generador y una corrida de 230 s se estiró más de dos horas. **No citable.** Con
réplicas estranguladas hay que elegir una carga que la topología final absorba
con holgura.
