# Microservicio de procesado (MQTT a InfluxDB)

Consume las lecturas biométricas publicadas por los simuladores en el topic
`hyrox/+/biometrics` y las persiste en InfluxDB. Dentro del clúster se conecta
por DNS interno (`mosquitto:1883`, `http://influxdb:8086`) y toma el token del
Secret `influxdb-auth`.

Se programa en el nodo `hyrox-worker`, mientras broker y base de datos están en
`k3s-hyrox`, de modo que el pipeline atraviesa la red entre nodos.

## Construir y publicar la imagen

El clúster es k3s (containerd) sin registry, así que la imagen se construye y se
importa en la containerd local de cada nodo. El Mac no tiene Docker, por lo
que se construye dentro de la VM. Las VMs son `linux/amd64` (igual que el Mac
Intel), así que no hace falta build multiarquitectura.

```bash
# 1. Empaquetar el contexto de build y enviarlo a la VM
tar czf /tmp/processor-ctx.tgz -C components/processor .
multipass transfer /tmp/processor-ctx.tgz k3s-hyrox:/tmp/processor-ctx.tgz

# 2. (solo la primera vez) instalar Docker en la VM para poder construir
multipass exec k3s-hyrox -- bash -lc 'command -v docker || (sudo apt-get update && sudo apt-get install -y docker.io)'

# 3. Construir la imagen
multipass exec k3s-hyrox -- bash -lc '
  rm -rf /tmp/processor-ctx && mkdir -p /tmp/processor-ctx &&
  tar xzf /tmp/processor-ctx.tgz -C /tmp/processor-ctx &&
  sudo docker build -t hyrox/processor:0.1.0 /tmp/processor-ctx'

# 4. Exportar la imagen e importarla en la containerd de k3s (namespace k8s.io)
multipass exec k3s-hyrox -- bash -lc '
  sudo docker save hyrox/processor:0.1.0 -o /tmp/processor.tar &&
  sudo k3s ctr -n k8s.io images import /tmp/processor.tar'

# 5. Importar también en el nodo worker (donde se ejecuta el pod)
multipass transfer k3s-hyrox:/tmp/processor.tar /tmp/processor.tar
multipass transfer /tmp/processor.tar hyrox-worker:/tmp/processor.tar
multipass exec hyrox-worker -- bash -lc 'sudo k3s ctr -n k8s.io images import /tmp/processor.tar'
```

## Desplegar

```bash
kubectl apply -f infra/manifests/processor/deployment.yaml \
              -f infra/manifests/processor/hpa.yaml
kubectl rollout status deployment/processor -n hyrox
kubectl logs -n hyrox deployment/processor -f
```

## Autoescalado con los atletas en pista (HPA)

A 1 lectura/s por atleta, la CPU del procesador es proporcional al número de
atletas en pista, así que el HPA (`hpa.yaml`) escala réplicas por utilización
de CPU. Dimensionado con la evaluación experimental:

- `requests.cpu: 200m` es la capacidad nominal de una réplica (~195 mCPU
  sosteniendo 1.000 atletas con pérdida 0; techo limpio ~1.600).
- Con el umbral al 75%, la segunda réplica entra al superar ~750 atletas,
  antes del pico observado en carreras HYROX (~800 simultáneos).
- En valle (<400 atletas) vuelve a 1 réplica (ventana de bajada de 3 min para
  no oscilar entre tandas de salida).

Requiere metrics-server (k3s lo trae de serie). Observar en vivo:

```bash
kubectl get hpa processor -n hyrox --watch
```

## Actualizar a una versión nueva

Reconstruir con una etiqueta nueva (`hyrox/processor:0.1.1`), repetir el import
en ambos nodos, actualizar el `image:` del Deployment y `kubectl apply`. La
infraestructura es inmutable: no se parchea el pod, se sustituye por la versión
nueva.

## Probar el pipeline ya desplegado

Con el procesador corriendo en el clúster, lanza solo simuladores (sin levantar
el procesador local). En `run_race.sh` el procesador local se puede omitir;
apunta los simuladores al NodePort del broker (`192.168.252.2:31883`).
