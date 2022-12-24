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
curl -s https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo apt-key add

# once there's support for it, 'xenial' here can be changed to 'jammy'
 
apt-add-repository "deb http://apt.kubernetes.io/ kubernetes-xenial main"
apt update
apt install -y kubelet kubeadm kubectl kubernetes-cni
apt-mark hold kubelet kubeadm kubectl

echo '>>>> Initialising kubernetes'

# join the cluster

mkdir -p /opt/backup/join-command
sshpass -p ${BACKUP_PASSWORD}  scp -o 'StrictHostKeyChecking no' ${BACKUP_USERNAME}@${BACKUP_HOST}:${BACKUP_PATH}/join-command/kubernetes-join-command.sh /opt/backup/join-command/kubernetes-join-command.sh

chmod a+x /opt/backup/join-command/kubernetes-join-command.sh
/opt/backup/join-command/kubernetes-join-command.sh


echo '>>>> ip addr show'
ip addr show

sleep 2

exit 0
