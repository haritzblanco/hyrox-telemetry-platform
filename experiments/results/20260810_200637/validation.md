# Matriz réplicas × carga con la plataforma arreglada — 2026-08-10

Barre la matriz de escalabilidad sobre la configuración de producción ya
corregida. Motivo de la campaña: **ninguna de las matrices anteriores sirve
para sostener el RNF-1**. Las tres del 12 de julio y la del 9 de agosto por la
tarde son anteriores al arreglo de la ventana de mensajes en vuelo del broker
(`0c44d1e`) y daban entre el 15 % y el 97 % de pérdida a partir de 800 msg/s;
la serie del 9 de agosto entre las 16:41 y las 17:22 quedó invalidada por la
saturación del equipo generador. Sin esta campaña, el capítulo de evaluación no
tenía ninguna curva de carga creciente que citar.

Arnés: `experiments/load-tests/run_matrix.sh` y `experiments/notebooks/analyze.py`.
Carga: 4, 8, 12 y 16 atletas × speedup 50 ≈ 200, 400, 600 y 800 msg/s, ~92 s por
celda. Réplicas fijadas en 1, 2 y 4 mediante la anotación
`autoscaling.keda.sh/paused-replicas`, no con `kubectl scale`: el HPA de KEDA
revertía el escalado manual a mitad de corrida.
Condiciones: relojes NTP verificados antes de la tanda (offsets crudos de 265 a
271 ms, `multipass exec` incluido) y Mac en reposo al arrancar (74 % de idle).
En las doce celdas el simulador confirma tantas lecturas como encola
(`encolado == ofrecido`), de modo que el generador no se saturó en ningún punto
y la latencia de transporte es imputable a la plataforma.

## Resultados

> **CORRECCIÓN 2026-09-08.** La columna de CPU se ha recalculado en régimen. La
> versión original promediaba todas las muestras de cada celda, arranque
> incluido, lo que subestimaba el consumo de forma desigual: la celda de una
> réplica a 200 msg/s daba 80 m frente a los 103 de su régimen. `aggregate_run`
> descarta ahora los 30 primeros segundos y promedia cada réplica por separado
> antes de sumar. Las cifras de pérdida, latencia y caudal no cambian.

| Réplicas | Carga | Pérdida | Transporte p95 | Persistencia p95 | e2e (cota sup.) | CPU/réplica |
|---|---|---|---|---|---|---|
| 1 | 200 | 0,14 % | 152 ms | 614 ms | 766 ms | 103 m |
| 1 | 400 | 0,00 % | 152 ms | 563 ms | 715 ms | 201 m |
| 1 | 600 | 0,00 % | 159 ms | 551 ms | 709 ms | 279 m |
| 1 | 800 | 0,00 % | 164 ms | 548 ms | **712 ms** | 333 m |
| 2 | 800 | 0,01 % | 165 ms | 573 ms | 738 ms | 269 m |
| 4 | 800 | 0,03 % | 172 ms | 637 ms | 809 ms | 182 m |

Umbrales: pérdida < 0,5 % (RNF-3) y latencia p95 < 2.000 ms (RNF-2). **Las doce
celdas los cumplen**, con dos órdenes de magnitud de margen en la pérdida y un
factor de 2,5 en la latencia.

## Lectura

**Una sola réplica sostiene el pico de diseño completo.** A 800 msg/s con una
réplica no se pierde ni una lectura y la latencia de extremo a extremo se queda
en 712 ms, con 333 mCPU frente a un límite de 1.000 m. El caudal confirmado en
régimen sigue la diagonal ideal en las cuatro cargas (807 msg/s medidos frente a
800 ofrecidos con una réplica).

**La latencia es plana frente a la carga.** El transporte se mueve entre 152 y
172 ms en toda la matriz, sin tendencia apreciable al cuadruplicar la carga: a
estas tasas el sistema no está cerca de ningún cuello de botella. La
persistencia se mantiene alrededor de 550-640 ms, gobernada por el medio segundo
de la escritura por lotes y no por la carga.

**Replicar compra margen, no caudal.** El consumo por réplica al pico baja de
333 m a 269 m con dos réplicas y a 182 m con cuatro, es decir, el trabajo se
reparte como cabe esperar de la suscripción compartida, pero el caudal agregado
no mejora porque no había nada que mejorar: ya estaba en el 100 %. El valor del
escalado horizontal en esta plataforma es la reserva de capacidad y la
tolerancia a que una réplica caiga, no un techo de caudal más alto.

**La pérdida más alta aparece en la carga más baja** (0,14 % con una réplica a
200 msg/s), que es la primera celda de la tanda. El patrón apunta al
establecimiento de las conexiones al arrancar la corrida y no a un límite de
capacidad, coherente con que las once celdas siguientes, todas más exigentes,
se queden en 0,03 % o menos.

## Desfase de la métrica de persistencia (hallazgo 2026-09-08)

La celda `R4_N4` marca 933 ms de persistencia frente a los ~620 del resto. No es
un reintento de la base de datos, como decía la primera lectura de esta campaña,
sino un **defecto de la instrumentación**.

Dentro de esa celda las réplicas forman dos poblaciones estables, en todas sus
ventanas: `82cmr` y `zpjbs` en 946 y 941 ms, `nns8h` y `nvsx8` en 516 y 519. El
exceso de las primeras sobre las segundas no es un tiempo constante sino una
cuenta de lecturas constante:

| Celda | Tasa por réplica | Lentas | Rápidas | Exceso | Exceso × tasa |
|---|---|---|---|---|---|
| R4_N4 | 50 msg/s | 943 ms | 518 ms | 426 ms | 21,3 lecturas |
| R4_N8 | 100 msg/s | 749 ms | 515 ms | 233 ms | 23,3 lecturas |
| R4_N12 | 150 msg/s | 688 ms | 532 ms | 156 ms | 23,4 lecturas |
| R4_N16 | 200 msg/s | 663 ms | 551 ms | 112 ms | 22,4 lecturas |

**Mecanismo.** `PersistenceTracker` empareja FIFO: `on_enqueue()` apila una marca
por punto y `on_ack(n)` saca las n más antiguas, con `n = _count_points(data)`
del lote confirmado. Si alguna confirmación declara menos puntos de los que se
encolaron, las marcas sobrantes se quedan a la cabeza de la cola **para siempre**
y cada medida posterior sale inflada en `desfase / tasa`, que es exactamente lo
que muestra la tabla. `on_ack` saca `min(n, len(pendientes))`, de modo que el
error en el otro sentido sí se autocorrige, pero este no.

**Alcance del error.** Acompaña a réplicas concretas, no a cargas concretas.
`82cmr` lo arrastra desde el principio de la tanda: en las celdas de una y dos
réplicas su exceso sobre la réplica sana da el mismo desfase de ~20 lecturas
(R1_N4: 614 vs ~515 a 200 msg/s; R2_N4: 721 vs 512 a 100 msg/s por réplica).
`zpjbs` lo adquiere entre la fila de dos y la de cuatro. Las dos réplicas creadas
al escalar a cuatro están limpias.

**Qué cifras quedan afectadas.** Solo la columna de persistencia y, por arrastre,
la cota de extremo a extremo. La persistencia real de las réplicas afectadas es
la de las sanas, ~515 ms. Las medidas de transporte, caudal y pérdida se obtienen
por vías independientes y no se ven tocadas. Ninguna conclusión cambia: incluso
con la cifra inflada, la celda peor cumple el umbral de 2 s con casi un factor
de dos de margen.

**Las cifras de la tabla de arriba son las medidas, sin corregir.** `analyze.py`
sabe descontar el desfase si se le pide (`--corregir-persistencia`): con la
corrección la persistencia de las doce celdas cae en 513-521 ms y la cota de
extremo a extremo en 669-696 ms, y el pico con una réplica pasa de 712 a 680 ms.
Se documenta aquí como comprobación del diagnóstico, pero **el capítulo cita las
cifras sin corregir** y explica el desajuste en el texto, que es más simple de
seguir que una columna corregida.

La corrección **no se aplica por defecto**: el mismo exceso lo produce una cola de
escritura real, y en una campaña con saturación provocada descontarlo borraría el
fenómeno medido. Aquí es legítimo porque la campaña acredita por otra vía que
ninguna réplica va atrasada: transporte plano en 152-175 ms y pérdida nula en las
doce celdas.

**Pendiente.** Exponer el tamaño de la cola de marcas pendientes en la línea de
métricas, para que el desfase sea detectable durante la corrida y no a
posteriori.

## Alcance

La matriz se detiene en 800 msg/s a propósito. Por encima de esa tasa el
limitante deja de ser la plataforma y pasa a ser el equipo que genera la carga:
las campañas anteriores que llegaban a 1.600 y 2.000 msg/s medían la saturación
del Mac, no la del clúster, y por eso sus cifras no son utilizables. El pico de
diseño del escenario (800 msg/s) queda cubierto; determinar el techo real de la
plataforma exigiría un generador en otra máquina.

Figuras del capítulo generadas con
`python3 experiments/notebooks/build_figures.py --matrix experiments/results/20260810_200637 ...`
en `docs/figuras/cap10/`.
