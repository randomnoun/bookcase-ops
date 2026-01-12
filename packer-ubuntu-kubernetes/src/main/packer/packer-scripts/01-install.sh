#!/bin/bash

set -o xtrace

# All output to LOGFILE and to terminal
LOGFILE=/opt/packer/packer-install-log.txt
mkdir -p /opt/packer
exec > >(tee ${LOGFILE}) 2>&1

echo '>>>> 01-install.sh'
echo "All output logged to ${LOGFILE}"

echo '>>>> Environment'
set

echo '>>>> ip addr show'
ip addr show

if [ `id -u` -ne 0 ] ; then echo "Please run as root using sudo" ; exit 1 ; fi

echo '>>>> Installing base apt packages'

apt-get update
apt-get install -y bc jq sshpass unzip
apt-get install -y gnupg2 software-properties-common apt-transport-https

echo '>>>> Creating /root/backup-variables.sh'

cat << EOF > /root/backup-variables.sh
#! /bin/bash
BACKUP_HOST=${BACKUP_HOST}
BACKUP_PATH=${BACKUP_PATH}
BACKUP_USERNAME=${BACKUP_USERNAME}
BACKUP_PASSWORD=${BACKUP_PASSWORD}
EOF

echo '>>>> Copying files from /opt/packer to filesystem'
 
mv /opt/packer/root/.inputrc ${HOME}/.inputrc

# taken from https://www.cherryservers.com/blog/install-kubernetes-ubuntu

echo '>>>> Installing NFS client'

apt install -y nfs-common

echo '>>>> Installing kernel modules'

sudo tee /etc/modules-load.d/containerd.conf <<EOF
overlay
br_netfilter
EOF
modprobe overlay
modprobe br_netfilter


echo '>>>> Setting kernel parameters'
 tee /etc/sysctl.d/kubernetes.conf <<EOF
net.bridge.bridge-nf-call-ip6tables = 1
net.bridge.bridge-nf-call-iptables = 1
net.ipv4.ip_forward = 1
EOF

sysctl --system

echo '>>>> Disabling swap'

swapoff -a
sed -i 's/^\(.*swap.*\)$/#\1/g' /etc/fstab


echo '>>>> Installing containerd.io'

curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmour -o /etc/apt/trusted.gpg.d/docker.gpg
add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable"
apt update
apt install -y containerd.io

echo '>>>> Configuring containerd.io'

containerd config default | sudo tee /etc/containerd/config.toml >/dev/null 2>&1
sed -i 's/SystemdCgroup \= false/SystemdCgroup \= true/g' /etc/containerd/config.toml

systemctl restart containerd
systemctl enable containerd


echo '>>>> Installing kubernetes'
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.35/deb/Release.key | sudo gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg

# ubuntu 24.04.3 == noble numbat

echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.35/deb/ /" | sudo tee /etc/apt/sources.list.d/kubernetes.list
apt update
apt install -y kubeadm kubelet kubectl
apt-mark hold kubelet kubeadm kubectl

echo '>>>> Creating config file'

# to recreate this config, run kubeadm config print init-defaults
# . set the ttl to 0
# . comment out the entire InitConfiguration block

IPADDRESS=$(hostname -I | tr ' ' '\n' | egrep -v '^172.16' | head -1)
cat << EOF > /tmp/kubeadm.config
apiVersion: kubeadm.k8s.io/v1beta4
kind: InitConfiguration
bootstrapTokens:
- groups:
  - system:bootstrappers:kubeadm:default-node-token
  token: ${KUBERNETES_TOKEN}
  ttl: 24h0m0s
  usages:
  - signing
  - authentication
localAPIEndpoint:
  advertiseAddress: ${IPADDRESS}
  bindPort: 6443
nodeRegistration:
  criSocket: unix:///var/run/containerd/containerd.sock
  imagePullPolicy: IfNotPresent
  imagePullSerial: true
  name: node
  taints: null
timeouts:
  controlPlaneComponentHealthCheck: 4m0s
  discovery: 5m0s
  etcdAPICall: 2m0s
  kubeletHealthCheck: 4m0s
  kubernetesAPICall: 1m0s
  tlsBootstrap: 5m0s
  upgradeManifests: 5m0s
---
apiVersion: kubeadm.k8s.io/v1beta4
kind: ClusterConfiguration
apiServer: {}
caCertificateValidityPeriod: 87600h0m0s
certificateValidityPeriod: 8760h0m0s
certificatesDir: /etc/kubernetes/pki
clusterName: ${KUBERNETES_CLUSTERNAME}
controlPlaneEndpoint: "${KUBERNETES_FQDN}"
controllerManager: {}
dns: {}
encryptionAlgorithm: RSA-2048
etcd:
  local:
    dataDir: /var/lib/etcd
imageRepository: registry.k8s.io
kubernetesVersion: 1.35.0
networking:
  dnsDomain: cluster.local
  serviceSubnet: 10.96.0.0/12
  podSubnet: 192.168.16.0/20
proxy: {}
scheduler: {}
EOF

cat /tmp/kubeadm.config

echo '>>>> Initialising kubernetes'

# kubeadm init --control-plane-endpoint=${KUBERNETES_FQDN}

# pod IPS allocated from 192.168.16.1 -> 192.168.31.254
kubeadm init --config=/tmp/kubeadm.config

echo '>>>> Writing kubeconfig'

echo HOME is $HOME
mkdir -p $HOME/.kube
cp -i /etc/kubernetes/admin.conf $HOME/.kube/config
# chown $(id -u):$(id -g) $HOME/.kube/config
chown -R ${CLOUD_INIT_USERNAME}:${CLOUD_INIT_USERNAME} $HOME/.kube 

kubectl cluster-info
kubectl get nodes


echo '>>>> Deploying pod network'

# via https://docs.tigera.io/calico/latest/getting-started/kubernetes/self-managed-onprem/onpremises

kubectl create -f https://raw.githubusercontent.com/projectcalico/calico/v3.31.3/manifests/operator-crds.yaml
kubectl create -f https://raw.githubusercontent.com/projectcalico/calico/v3.31.3/manifests/tigera-operator.yaml

# curl -O https://raw.githubusercontent.com/projectcalico/calico/v3.31.3/manifests/custom-resources-bpf.yaml
# kubectl create -f custom-resources-bpf.yaml
kubectl create -f /opt/packer/calico-install/custom-resources-bpf.yaml

# watch kubectl get tigerastatus

kubectl get pods -n kube-system
 
# watch kubectl get pods -n calico-system
# may take a few seconds or a minute

echo '>>>> Waiting for control-plane'

# When all of the STATUS column shows ‘Running,’ it’s an indication that everything is finished deploying and good to go.
kubectl wait --namespace=kube-system --for=condition=Ready pods --selector tier=control-plane --timeout=600s

# coredns is scheduled on non-controlplane nodes
# kubectl wait --namespace=kube-system --for=condition=Ready pods --selector k8s-app=kube-dns --timeout=600s
# kubectl wait --namespace=kube-system --for=condition=Ready pods --selector k8s-app=calico-node --timeout=600s
# kubectl wait --namespace=kube-system --for=condition=Ready pods --selector k8s-app=calico-kube-controllers --timeout=600s
 
# when this runs there are still 'Pending' pods ( coredns-xxx )
kubectl get pods --all-namespaces

echo '>>>> Creating join-command'

export CERT_HASH=$(openssl x509 -pubkey -in /etc/kubernetes/pki/ca.crt | openssl rsa -pubin -outform der 2>/dev/null | openssl dgst -sha256 -hex | sed 's/^.* //')

mkdir -p /opt/backup/join-command
cat << EOF > /opt/backup/join-command/kubernetes-join-command.sh
#!/bin/sh
#
# kubernetes-join-command.sh
#
# run this from within a new worker node to cause that node to join the ${KUBERNETES_FQDN} cluster
kubeadm join ${KUBERNETES_FQDN}:6443 --token ${KUBERNETES_TOKEN} --discovery-token-ca-cert-hash sha256:${CERT_HASH}
EOF
# ^ using long-lived token instead
# kubeadm token create --print-join-command >> /opt/backup/join-command/kubernetes-join-command.sh

sshpass -p ${BACKUP_PASSWORD} ssh -o 'StrictHostKeyChecking no' ${BACKUP_USERNAME}@${BACKUP_HOST} mkdir -p ${BACKUP_PATH}/join-command
sshpass -p ${BACKUP_PASSWORD}  scp -o 'StrictHostKeyChecking no' /opt/backup/join-command/kubernetes-join-command.sh ${BACKUP_USERNAME}@${BACKUP_HOST}:${BACKUP_PATH}/join-command/kubernetes-join-command.sh
sshpass -p ${BACKUP_PASSWORD}  scp -o 'StrictHostKeyChecking no' ${HOME}/.kube/config ${BACKUP_USERNAME}@${BACKUP_HOST}:${BACKUP_PATH}/kubeconfig

echo '>>>> ip addr show'
ip addr show

sleep 2

exit 0

