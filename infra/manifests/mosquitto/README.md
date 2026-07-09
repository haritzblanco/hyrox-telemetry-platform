# Mosquitto MQTT Broker

Broker de mensajería del sistema. Implementación de referencia del estándar
MQTT mantenida por la Eclipse Foundation. Seleccionado por su footprint
mínimo de recursos y simplicidad operativa, lo que permite aislar el
comportamiento de la plataforma del consumo del propio broker en las
mediciones experimentales.

## Componentes

- `namespace.yaml`: namespace `hyrox` que aísla los recursos de la plataforma
- `configmap.yaml`: configuración del broker (`mosquitto.conf`)
- `deployment.yaml`: pod del broker (imagen `eclipse-mosquitto:2.0.18`)
- `service.yaml`: servicio ClusterIP en el puerto 1883

## Despliegue

```bash
kubectl apply -f infra/manifests/mosquitto/
```

## Verificación

```bash
kubectl get pods -n hyrox
kubectl logs -n hyrox -l app.kubernetes.io/name=mosquitto
```

## Acceso desde el host de desarrollo

Túnel mediante port-forward:

```bash
kubectl port-forward -n hyrox service/mosquitto 1883:1883
```

Publish/subscribe de prueba:

```bash
mosquitto_sub -h localhost -p 1883 -t "hyrox/+/heartrate" -v
mosquitto_pub -h localhost -p 1883 -t "hyrox/atleta-001/heartrate" -m '{"bpm":152}'
```

## Limitaciones conocidas (entorno de desarrollo)

- `allow_anonymous true`: sin autenticación. Aceptable en clúster local
  aislado; debe revisarse para despliegue productivo.
- `emptyDir` para persistencia: los datos se pierden al recrearse el pod.
  Suficiente para esta fase; se sustituirá por PersistentVolumeClaim si
  el caso de uso lo requiere.
- Una sola réplica. La escalabilidad se aborda a nivel de los consumidores
  y no del broker, por las razones documentadas en la memoria.
