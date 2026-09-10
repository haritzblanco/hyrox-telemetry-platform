# La medida del pico depende del estado del banco — 2026-09-10

Remedida de las tres corridas al pico con la configuración de producción, ya con
el broker fijado al nodo de control y con el reparto de pods registrado en cada
corrida. El objetivo era blindar las cifras de `*_peak_prod_frio_*`, que se
tomaron antes del arreglo y sin poder demostrar dónde estaba el broker.

## No se reproducen

| Corrida | Pérdida | Transporte p95 | Persistencia p95 | Reparto |
|---|---|---|---|---|
| 1 | 0,00 % | 8,1 s | 796 ms | 1 réplica en worker |
| 2 | 0,00 % | 10,9 s | 814 ms | 2+2 en los dos worker |
| 3 | **7,20 %** | 42,5 s | 812 ms | 2+2 en los dos worker |

El broker estaba en el nodo de control en las tres, así que **el reparto ya no es
la explicación**. Frente a las corridas de la tarde, que dieron 0 % de pérdida y
159-177 ms de transporte, aquí la persistencia sube de 530-535 ms a 796-814 ms y
el transporte se va a segundos. Si escribir cuesta la mitad más, la cola del
escritor se llena, el consumo se frena y el retraso aparece como latencia de
transporte.

## Dos candidatos, sin separar

**El depósito.** InfluxDB tiene 503 MB de motor y 7,59 millones de puntos, casi
todos escritos hoy: unas 45 corridas a 160 mil lecturas cada una. Los
experimentos escriben en el mismo bucket que la demo, de modo que **cada campaña
contamina a la siguiente**.

**El anfitrión.** Entre las corridas buenas y estas median tres horas más de
trabajo continuo del Mac.

No se pueden separar sin más medidas, y las corridas de la tarde debilitan la
primera hipótesis por sí sola: dieron 530 ms con el depósito ya bastante lleno.

## Qué hacer antes de la campaña definitiva

En una sola tacada y por este orden: purgar del bucket las sesiones de
experimento (se reconocen por los prefijos `exp_`, `cal_`, `inf_`, `peak_`),
reiniciar las VMs, sincronizar relojes y medir **lo primero** las tres corridas
al pico. Mejor todavía, que el arnés escriba en un bucket aparte que se tire
entre campañas: quitaría esa variable para siempre.

Mientras tanto, las cifras que el capítulo puede citar son las de
`*_peak_prod_frio_*`, coherentes con los 164 ms de la campaña de agosto, y
declarando el protocolo con el que se tomaron.
