# Validación del autoescalado (HPA) del procesador — 2026-07-09

Valida `infra/manifests/processor/hpa.yaml`: el número de réplicas del
procesador sigue a los atletas en pista (1 atleta ≈ 1 msg/s a 1 Hz).

## Escenario

- Carga: 20 atletas × speedup 60 ≈ **1.200 msg/s sostenidos** (`LOOP=true`),
  equivalentes a 1.200 atletas simultáneos — por encima del pico investigado
  en carreras HYROX (~800) y del umbral de escalado (~750).
- HPA: request 200m (capacidad nominal de una réplica), umbral 75%,
  min 1 / max 4, scale-up dobla cada 30 s, scale-down 1 pod/60 s tras
  ventana de estabilización de 180 s.

## Línea temporal observada

| Hora     | Evento                                   | Utilización (media/réplica) |
|----------|------------------------------------------|------------------------------|
| 20:06    | Reposo                                   | 2%/75%, 1 réplica            |
| ~20:07   | Arranca la carga (1.200 msg/s)           | 82% → 183% → 198%            |
| 20:08:13 | **Escala 1 → 2**                         | 198%                         |
| 20:08:42 | **Escala 2 → 4** (dobla a los 30 s)      | 141%                         |
| 20:08:52 | Régimen estable con carga                | 78%/75%, 4 réplicas          |
| ~20:09   | Se corta la carga                        | cae a 1%                     |
| 20:12:34 | **Escala 4 → 3** (tras 180 s de calma)   | 1%                           |
| 20:13:35 | **Escala 3 → 2** (+60 s)                 | 1%                           |
| 20:14:35 | **Escala 2 → 1** (+60 s)                 | 1%                           |

Ciclo completo: **~1 min** para reaccionar a la entrada de la carga,
**~5,5 min** para devolver los recursos tras el vaciado de la pista, con
los tiempos exactos que dictan las políticas del manifiesto.

## Eventos oficiales del HPA (`kubectl describe hpa processor -n hyrox`)

```
Normal  SuccessfulRescale  New size: 2; reason: cpu resource utilization (percentage of request) above target
Normal  SuccessfulRescale  New size: 4; reason: cpu resource utilization (percentage of request) above target
Normal  SuccessfulRescale  New size: 3; reason: All metrics below target
Normal  SuccessfulRescale  New size: 2; reason: All metrics below target
Normal  SuccessfulRescale  New size: 1; reason: All metrics below target
```

(Los dos Warnings `FailedGetResourceMetric` iniciales son el arranque de
metrics-server tras el boot de las VMs, previos a la carga.)

## Observaciones

- El reparto entre réplicas lo hace la suscripción MQTT compartida
  (`$share/processors`): sin escrituras duplicadas.
- Con 4 réplicas en el worker de 2 vCPU la utilización media por réplica en
  régimen (78%) es mayor que la que sugerirían las medidas de 1 réplica
  (~337m totales a 1.200 msg/s): las réplicas compiten por CPU en el mismo
  nodo — consistente con el hallazgo de la evaluación de que en un clúster
  pequeño la colocación pesa más que el número de réplicas.
- La validación es **funcional** (el mecanismo reacciona a la señal correcta
  con los tiempos configurados); el beneficio cuantitativo del escalado
  sigue limitado por el testbed mono-portátil (ver README de experiments).

---

# Segunda validación: reparto entre workers (mismo día, más tarde)

Se añadió un segundo nodo de cómputo (`hyrox-worker2`, multipass 2 vCPU/2 GB,
k3s agent v1.35.5) y el Deployment pasó de fijarse por hostname a:

- `nodeSelector: {node-role: compute}` (ambos workers etiquetados), y
- `podAntiAffinity` preferida entre réplicas (`topologyKey: hostname`).

## Confound del host detectado (esperado)

Con la 3ª VM, el Mac (4 núcleos físicos, loadavg ~4,2) ya no genera los
1.200 msg/s plenos: al procesador llegaban ~500-700 msg/s (~88 mCPU, 44% del
request de 200m) y mosquitto subió a ~500 mCPU — el cuello se movió aguas
arriba, consistente con la evaluación de junio.

## Metodología de capacidad acotada

En lugar de subir la carga (imposible en este host), se **acota la capacidad
unitaria** de la réplica: `requests.cpu: 100m`, `limits.cpu: 150m`
(temporal, solo para la demo). Así la carga real disponible supera el umbral
y se observa la *proporcionalidad* del escalado, no el techo absoluto —
técnica estándar de testbed reducido.

## Resultado

| Hora     | Evento                                                        |
|----------|---------------------------------------------------------------|
| 21:31:57 | 1 réplica al 126% (por encima de su capacidad acotada)         |
| 21:32:07 | **Escala a 2: 1×hyrox-worker + 1×hyrox-worker2** (anti-afinidad) |
| 21:32:48 | **Escala a 4: 2×hyrox-worker + 2×hyrox-worker2** (reparto simétrico) |
| 21:32:58 | Régimen: 4 réplicas ~92-137 mCPU cada una (al límite acotado)  |

Dato clave: el consumo agregado pasó de ~88 mCPU (1 réplica) a **~490 mCPU
(4 réplicas en 2 nodos)** drenando la cola acumulada → el throughput agregado
crece con réplicas cuando se colocan en nodos distintos, que es exactamente
lo que el `nodeSelector` por hostname impedía demostrar.

```
processor-...-fldtm   1/1  Running   hyrox-worker    129m
processor-...-kvp9m   1/1  Running   hyrox-worker    130m
processor-...-2t58h   1/1  Running   hyrox-worker2   137m
processor-...-t5bgj   1/1  Running   hyrox-worker2    92m
```

Tras la demo se revirtieron los recursos a los dimensionados de producción
(request 200m) re-aplicando `deployment.yaml`.
