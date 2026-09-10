# El broker sin fijar era la variable oculta — 2026-09-10

Durante toda la jornada las medidas por réplica saltaban entre dos estados sin
explicación: un techo de ~850 msg/s con la CPU en 500 m y el transporte en 150
ms, o uno de 390-600 msg/s quemando 700-970 m y con el transporte en 13-28 s. Se
atribuyó primero a la cuota de CPU y después al estado del anfitrión. No era ni
lo uno ni lo otro.

## Qué pasaba

El Deployment de mosquitto **no tenía `nodeSelector`, ni afinidad, ni
tolerations**, y en k3s el nodo de control no está marcado, así que acepta pods.
Cada reinicio del broker era una moneda al aire entre `k3s-hyrox` y un nodo de
cómputo. Cuando caía en un nodo de cómputo compartía sus dos vCPU con una réplica
del procesador, y las dos se estorbaban.

El diseño lo daba por supuesto: el comentario de `processor-exp.yaml` dice que
broker e InfluxDB quedan aislados en `k3s-hyrox`. InfluxDB lo está de hecho,
porque su volumen local lo ata a un nodo; el broker no lo estaba por nada.

## La prueba

Con la réplica del procesador fijada a cada nodo, la ventana en vuelo de
producción y sin tocar la CPU, dos corridas por nodo:

| Nodo de la réplica | Techo | CPU | Transporte p95 |
|---|---|---|---|
| `hyrox-worker` (libre) | 853,3 y 853,4 msg/s | 514-540 m | 139-186 ms |
| `hyrox-worker2` (con el broker) | 571,1 y 603,4 msg/s | 963-969 m | 13-15 s |

Casi el doble de CPU para dos tercios del caudal, y dos órdenes de magnitud de
latencia. La reproducibilidad dentro de cada nodo es casi exacta, que es
justamente lo que no se conseguía mientras la variable estaba oculta.

Antes de esa prueba, tres corridas idénticas (misma ventana, mismas condiciones)
habían dado 435, 857 y 403 msg/s: alternaban porque cada reinicio del procesador
lo movía de un nodo al otro.

## El arreglo, y su verificación

`nodeSelector: node-role.kubernetes.io/control-plane: "true"` en el Deployment
del broker. Con él, tres corridas seguidas sin fijar el procesador dan 831, 854 y
823 msg/s con 149-168 ms de transporte: la alternancia desaparece.

## Qué invalida

Esto explica en retrospectiva casi todo lo raro de la jornada, y obliga a
descartar medidas:

- La calibración del límite de CPU, que no era monótona y se invertía entre
  sesiones (`20260910_094811`, `20260910_095357`, `20260910_155314`). Medía en
  qué nodo caía la réplica, no la cuota.
- La matriz estrangulada `20260910_100630` y la que la sustituía a medias
  (`20260910_154321`, interrumpida). Sus celdas mezclan réplicas con el broker al
  lado y sin él.
- La dispersión de las corridas de escalado automático repetidas, que llegaba a
  quince puntos de pérdida entre corridas idénticas.
- La celda `R4_N12` que se hundía por debajo de la diagonal con pérdida cero.

**No invalida** las corridas al pico con la configuración de producción medidas
en frío (`*_peak_prod_frio_*`): sus tres repeticiones dieron pérdida cero y
159-177 ms de transporte, cifras del estado bueno y coherentes con los 164 ms de
agosto.

## Lo que hay que registrar de ahora en adelante

El nodo de cada pod, en cada corrida. El arnés no lo guardaba, y por eso costó
un día entero encontrarlo.
