# La plataforma al pico de diseño, con el banco en frío — 2026-09-10

Repetición de `*_peak_produccion_*` con el banco reiniciado: VMs paradas, el Mac
dejado asentar, clúster arrancado de nuevo, relojes sincronizados y tres minutos
de asentamiento antes de medir. Configuración desplegada sin tocar nada:
procesador `0.11.0` con límite de 1000 m, exportador `0.3.0`, objetivo del
disparador en 400 msg/s por réplica, KEDA al mando, 40 atletas × speedup 20.

## Cumple los tres requisitos

| Corrida | Réplicas al empezar | Pérdida | Transporte p95 | Persistencia p95 | Extremo a extremo p95 |
|---|---|---|---|---|---|
| 1 | 1 (arranque en frío) | **0,00 %** | 159 ms | 530 ms | 689 ms |
| 2 | 4 | **0,00 %** | 173 ms | 533 ms | 706 ms |
| 3 | 4 | **0,00 %** | 177 ms | 535 ms | 712 ms |

Ninguna lectura perdida de 159.787 ofrecidas en las tres corridas, contra un
umbral del 0,5 % (RNF-3). La cota de extremo a extremo se queda en 689-712 ms
frente a los 2 s del umbral (RNF-2). Y el arranque en frío no cuesta nada: la
primera corrida empezó con una réplica y no perdió una sola lectura, porque una
réplica a 1000 m ya absorbe el pico entero. KEDA escaló igualmente a cuatro, pero
para entonces no había nada que rescatar.

## Era el banco, no la plataforma

Las mismas tres corridas con el Mac tras siete horas y media de campaña daban
5,46 / 1,55 / 0,00 % de pérdida y **27-39 s** de transporte p95. Con el banco en
frío salen 159-177 ms, que coincide con los 164 ms que midió la campaña de agosto
con la misma configuración de producción. Queda así confirmado que aquellas
cifras eran del anfitrión degradado.

**Corrección a lo que se supuso entonces.** Se apuntó como firma del anfitrión
saturado que el caudal ofrecido real cayera por debajo del nominal (683 msg/s de
800). No lo es: las tres corridas en frío ofrecen exactamente los mismos
683 msg/s. Ese 15 % es el estiramiento normal de la sesión simulada y no dice
nada del estado del banco. La firma buena es la latencia de transporte: si crece
de forma monótona a lo largo de la corrida mientras los procesadores están
ociosos, hay que parar y reiniciar el banco.

## Cómo medir esto en el futuro

Las corridas al pico con la configuración de producción se miden con el clúster
recién arrancado y como primera medida de la sesión. No es una manía: entre
medirlas en frío y medirlas al final de una jornada de campaña hay dos órdenes de
magnitud en la latencia de transporte.
