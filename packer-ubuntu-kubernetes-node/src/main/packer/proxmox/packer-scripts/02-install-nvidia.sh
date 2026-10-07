#!/bin/bash

set -o xtrace

# All output to LOGFILE and to terminal
LOGFILE=/opt/packer/packer-install-log.txt
mkdir -p /opt/packer
exec > >(tee -a ${LOGFILE}) 2>&1

echo '>>>> 02-install-nvidia.sh'

if [ `id -u` -ne 0 ] ; then echo "Please run as root using sudo" ; exit 1 ; fi

echo '>>>> Blacklisting nouveau'

cat << EOF > /etc/modprobe.d/blacklist-nouveau.conf
blacklist nouveau
options nouveau modeset=0
EOF

update-initramfs -u

echo '>>>> Installing NVIDIA driver and CUDA toolkit'

# No GPU is attached at image-build time (see the pci_devices note in
# proxmox-ubuntu-kubernetes-node.pkr.hcl) so ubuntu-drivers' autodetect
# can't find the 3090s here; install the driver package explicitly instead.
# The kernel module simply won't load until a GPU is attached post-clone.

apt-get update
apt-get install -y nvidia-driver-550 nvidia-cuda-toolkit

echo '>>>> Installing nvidia-container-toolkit'

# needed so containerd can actually hand GPUs to containers (kubernetes'
# nvidia-device-plugin DaemonSet and any GPU-requesting pod rely on this -
# without it the driver/CUDA toolkit alone are not enough)

curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

apt-get update
apt-get install -y nvidia-container-toolkit

# 01-install.sh (which runs before this script) already installs and starts
# containerd, so it's safe to patch its config here and restart it
nvidia-ctk runtime configure --runtime=containerd
systemctl restart containerd

echo '>>>> nvidia packages installed; driver will attach once GPUs are passed through post-clone'
