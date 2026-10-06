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

echo '>>>> nvidia packages installed; driver will attach once GPUs are passed through post-clone'
