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

| Réplicas | Carga | Pérdida | Transporte p95 | Persistencia p95 | e2e (cota sup.) | CPU/réplica |
|---|---|---|---|---|---|---|
| 1 | 200 | 0,14 % | 152 ms | 614 ms | 766 ms | 80 m |
| 1 | 400 | 0,00 % | 152 ms | 563 ms | 715 ms | 167 m |
| 1 | 600 | 0,00 % | 159 ms | 551 ms | 709 ms | 248 m |
| 1 | 800 | 0,00 % | 164 ms | 548 ms | **712 ms** | 293 m |
| 2 | 800 | 0,01 % | 165 ms | 573 ms | 738 ms | 230 m |
| 4 | 800 | 0,03 % | 172 ms | 637 ms | 809 ms | 157 m |

Umbrales: pérdida < 0,5 % (RNF-3) y latencia p95 < 2.000 ms (RNF-2). **Las doce
celdas los cumplen**, con dos órdenes de magnitud de margen en la pérdida y un
factor de 2,5 en la latencia.

## Lectura

**Una sola réplica sostiene el pico de diseño completo.** A 800 msg/s con una
réplica no se pierde ni una lectura y la latencia de extremo a extremo se queda
en 712 ms, con 293 mCPU frente a un límite de 1.000 m. El caudal confirmado en
régimen sigue la diagonal ideal en las cuatro cargas (807 msg/s medidos frente a
800 ofrecidos con una réplica).

**La latencia es plana frente a la carga.** El transporte se mueve entre 152 y
172 ms en toda la matriz, sin tendencia apreciable al cuadruplicar la carga: a
estas tasas el sistema no está cerca de ningún cuello de botella. La
persistencia se mantiene alrededor de 550-640 ms, gobernada por el medio segundo
de la escritura por lotes y no por la carga.

**Replicar compra margen, no caudal.** El consumo por réplica al pico baja de
293 m a 230 m con dos réplicas y a 157 m con cuatro, es decir, el trabajo se
reparte como cabe esperar de la suscripción compartida, pero el caudal agregado
no mejora porque no había nada que mejorar: ya estaba en el 100 %. El valor del
escalado horizontal en esta plataforma es la reserva de capacidad y la
tolerancia a que una réplica caiga, no un techo de caudal más alto.

**La pérdida más alta aparece en la carga más baja** (0,14 % con una réplica a
200 msg/s), que es la primera celda de la tanda. El patrón apunta al
establecimiento de las conexiones al arrancar la corrida y no a un límite de
capacidad, coherente con que las once celdas siguientes, todas más exigentes,
se queden en 0,03 % o menos.

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
