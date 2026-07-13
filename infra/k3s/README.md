# Despliegue del clúster K3s

El clúster se despliega sobre tres máquinas virtuales Ubuntu gestionadas con Multipass:
un nodo servidor (plano de control, que además aloja los servicios con estado) y dos
nodos de cómputo para el procesador. Esto permite trabajar desde macOS con un entorno
Linux real, requisito de K3s.

## Requisitos previos

- macOS con Homebrew
- Multipass: `brew install --cask multipass`
- kubectl: `brew install kubectl`

## Despliegue de las VMs

```bash
multipass launch 22.04 --name k3s-hyrox     --cpus 2 --memory 4G --disk 20G
multipass launch 22.04 --name hyrox-worker  --cpus 2 --memory 2G --disk 10G
multipass launch 22.04 --name hyrox-worker2 --cpus 2 --memory 2G --disk 10G
```

Apunta las IPs de `multipass list`; los pasos siguientes usan la del servidor
(en este proyecto, 192.168.252.2).

## Instalación de K3s

Servidor:

```bash
multipass exec k3s-hyrox -- bash -c "curl -sfL https://get.k3s.io | sh -"
multipass exec k3s-hyrox -- sudo cat /var/lib/rancher/k3s/server/node-token
```

Agentes, con el token del comando anterior:

```bash
multipass exec hyrox-worker -- bash -c \
  "curl -sfL https://get.k3s.io | K3S_URL=https://192.168.252.2:6443 K3S_TOKEN=<token> sh -"
multipass exec hyrox-worker2 -- bash -c \
  "curl -sfL https://get.k3s.io | K3S_URL=https://192.168.252.2:6443 K3S_TOKEN=<token> sh -"
```

Etiqueta de rol de los nodos de cómputo (la usa el nodeSelector del procesador):

```bash
kubectl label node hyrox-worker hyrox-worker2 node-role=compute
```

## Configuración de kubectl en el host

```bash
mkdir -p ~/.kube
multipass transfer k3s-hyrox:/etc/rancher/k3s/k3s.yaml ~/.kube/config-k3s-hyrox
sed -i '' "s/127.0.0.1/192.168.252.2/g" ~/.kube/config-k3s-hyrox
export KUBECONFIG=~/.kube/config-k3s-hyrox
```

## Verificación

```bash
kubectl get nodes   # los 3 nodos en Ready, los workers con node-role=compute
```

## Relojes de las VMs

Al suspender y reanudar el portátil, el reloj de las VMs puede quedar desplazado
varios segundos, y eso rompe las medidas de latencia de transporte. Antes de medir:

```bash
for vm in k3s-hyrox hyrox-worker hyrox-worker2; do
  multipass exec $vm -- sudo systemctl restart systemd-timesyncd
done
```

## Detener / arrancar / eliminar

```bash
multipass stop k3s-hyrox hyrox-worker hyrox-worker2
multipass start k3s-hyrox hyrox-worker hyrox-worker2
multipass delete k3s-hyrox hyrox-worker hyrox-worker2 && multipass purge
```
