# Corridas al pico con la configuración de producción — 2026-09-10

Tres corridas seguidas con la configuración desplegada: procesador `0.11.0` con
límite de 1000 m, exportador `0.3.0`, objetivo del disparador en 400 msg/s por
réplica, KEDA al mando sin réplicas fijadas, 40 atletas × speedup 20 durante unos
230 s. Datasets `*_peak_produccion_1`, `_2` y `_3`.

## Resultados

| Corrida | Réplicas al empezar | Pérdida | Transporte p95 | Persistencia p95 |
|---|---|---|---|---|
| 1 | 1 (arranque en frío) | 5,46 % | 33,4 s | 775 ms |
| 2 | 4 (heredadas) | 1,55 % | 39,2 s | 773 ms |
| 3 | 4 (heredadas) | **0,00 %** | 27,4 s | 806 ms |

Solo la primera es un arranque en frío: entre corridas median 30 s y KEDA no
alcanza a reducir las réplicas, así que la segunda y la tercera empiezan ya con
cuatro. La primera escaló a 2 réplicas a los 24 s y a 4 a los 63 s, y sus 5,46 %
se pierden en ese intervalo.

En caliente la plataforma **absorbe el pico de diseño sin perder una sola
lectura**, con los procesadores muy lejos de su límite: 249 m de media por
réplica de un límite de 1000 m.

## Estas corridas NO sirven para la latencia del capítulo

La latencia de transporte no es creíble y contradice a la campaña de agosto, que
con la misma configuración de producción y **una sola réplica** medía 164 ms de
p95 a 800 msg/s (`docs/figuras/cap10/datos-matriz.csv`). Aquí, con cuatro
réplicas, salen 27-39 s: 167 veces peor.

Se descartaron las dos explicaciones baratas. **No es deriva de reloj**: medida
justo después de las corridas, la sincronía seguía en 264-283 ms en las tres VMs,
igual que por la mañana, y el desfase crudo lo domina el coste de `multipass
exec`. **No es el procesado**: las réplicas consumieron 249 m de media de sus
1000 m, y la persistencia se mantuvo plana en torno a 800 ms. Los mensajes
esperaban en el broker mientras los consumidores estaban ociosos.

Lo que sí es sospechoso es el estado del anfitrión. El Mac llevaba siete horas y
media generando carga sin parar, con tres VMs y un simulador de cuarenta clientes
sobre cuatro núcleos, y su carga media rondaba 4,5. Se nota también en el caudal
ofrecido: 683 msg/s reales frente a los 800 nominales, un 15 % por debajo.

## Qué hacer antes de escribir el capítulo

Repetir estas tres corridas **en frío**: máquina recién arrancada, sin nada más
en marcha y como primera medida de la sesión, que es como se obtuvieron las
cifras limpias de agosto. Hasta entonces, la afirmación de que la plataforma
cumple el RNF-2 al pico de diseño se apoya en datos de la época del escritor
antiguo, y la de pérdida cero en caliente, en una sola corrida contaminada.
