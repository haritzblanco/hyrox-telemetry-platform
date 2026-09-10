# Los nodos de cómputo no están dedicados — 2026-09-10

Continuación de `20260910_192422_broker_coubicado`. Fijar el broker al nodo de
control quitó de en medio la degradación grande, pero quedaba una diferencia
persistente entre los dos nodos de cómputo. Esta es su causa.

## Medida pareada

Réplica única fijada alternativamente a cada nodo, misma carga (16 atletas ×
speedup 50), broker ya aislado, corridas contiguas:

| Vuelta | `hyrox-worker` | `hyrox-worker2` |
|---|---|---|
| 1 | 946 msg/s, 436 m, 510 ms | 665 msg/s, 881 m, 13,3 s |
| 2 | 969 msg/s, 459 m | 650 msg/s, 941 m, 12,4 s |

Un nodo entrega la mitad más que el otro gastando la mitad de CPU. Seis corridas
seguidas en `hyrox-worker2` dieron 629-719 msg/s, una banda estrecha: no es
biestabilidad, es un nivel más bajo y estable.

## No es la máquina virtual

Las dos VMs tienen 2 vCPU y 2 GB, y son igual de rápidas: un bucle de cinco
millones de iteraciones tarda 0,95 s en una y 0,96 s en la otra, con el contador
de robo de CPU a cero en ambas. La red tampoco: 1,07 ms de RTT al nodo de control
desde las dos, con los mismos MTU.

## Es lo que corre al lado

Los nodos de cómputo alojan además el plano de control y la observabilidad, y el
reparto es desigual:

| `hyrox-worker` | `hyrox-worker2` |
|---|---|
| argocd-applicationset-controller | **argocd-application-controller** |
| argocd-server | argocd-dex-server |
| keda-operator y su apiserver | argocd-notifications-controller |
| prometheus-server | argocd-redis |
| node-exporter, svclb | kube-state-metrics, node-exporter, svclb |

`argocd-application-controller` es el componente que más CPU consume de ArgoCD,
porque compara continuamente el estado deseado con el real. Se nota en reposo: la
carga media de `hyrox-worker2` es 1,20 frente a 0,35 de `hyrox-worker`, sin
ninguna carga de la plataforma.

## Consecuencias para la evaluación

**Las dos configuraciones de más de una réplica incluyen siempre el nodo lento.**
Con anti-afinidad y dos nodos de cómputo, dos réplicas caen una en cada uno, y
cuatro se reparten dos y dos. Su caudal por réplica queda lastrado por
`hyrox-worker2` en una proporción que no tiene nada que ver con la plataforma.

**Una réplica sola es una lotería** entre 950 y 660 msg/s según dónde la coloque
el planificador, y eso explica buena parte de la dispersión de todas las campañas
anteriores.

## Qué hacer

Para medir, fijar la réplica a un nodo concreto y registrarlo, que es lo que ya
hace el arnés desde `fix(arnés)`. Comparar solo corridas del mismo nodo.

Mover ArgoCD, KEDA y Prometheus al nodo de control **no** es buena idea sin más:
ahí están InfluxDB, Grafana y ahora el broker, y ese nodo tiene también 2 vCPU.
Se cambiaría un problema por otro, y el broker es justo lo que acabamos de
proteger.

Lo honesto en la memoria es declararlo como limitación del banco: un clúster de
tres máquinas virtuales sobre un portátil de cuatro núcleos no puede dedicar
nodos a la carga de trabajo, y las cifras por réplica dependen de con quién
comparte nodo la réplica.
