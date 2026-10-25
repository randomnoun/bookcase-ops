# bookcase-ops LLM kubernetes node

Part of the [LLM setup](SETUP-LLM.md).

This covers creating `bnenod05`, the kubernetes node that runs the GPU workloads, as a VM on the Proxmox host `bnellm01`. It assumes Proxmox is already installed, see [SETUP-PROXMOX.md](SETUP-PROXMOX.md).

## Kubernetes node on proxmox

Check the [../packer-ubuntu-kubernetes-node/README.md](../packer-ubuntu-kubernetes-node/README.md) on how to configure it, from that folder you'll need to run 

```
build.sh proxmox
```

to create the `tpl-ubuntu-kubernetes-node` template. 

Clone and rename it to create `bnenod05`, start it, and join the cluster by running ` /opt/backup/join-command/kubernetes-join-command.sh`

If you need to regenerate the token, from `bnekub03`, run `kubeadm token create --print-join-command`

If starting the node appears to crash the host (including proxmox), try decreating the memory allocation a bit

## Trusting the randomnoun CA

Nodes need to trust the CA that signed the internal certificates, or containerd can't pull images from nexus3 ( it fails with an x509 error ). 

Nodes built from the packer template already do: `01-install.sh` copies `cacert.pem` from the NAS ( `nas_ca_path` in the packer vars file ) into the trust store 
before containerd is installed. Nodes built before that change ( `bnekub03`, `bnenod04`, and `bnenod05` if it predates it ) need it installing by hand:

```
# from a machine that has the CA: the file must end in .crt for update-ca-certificates to pick it up
scp cacert.pem knoxg@bnenod05.dev.randomnoun:/tmp/cacert.crt

# then on the node
sudo cp /tmp/cacert.crt /usr/local/share/ca-certificates/cacert.crt
sudo update-ca-certificates
sudo systemctl restart containerd
```

containerd only reads the trust store when it starts, so the restart is needed; running pods aren't affected by it. To check:

```
curl -sI https://docker-combined.nexus3.dev.randomnoun/v2/ | head -1
sudo ctr -n k8s.io images pull docker-combined.nexus3.dev.randomnoun/sonar:0.24.1
```

The `curl` should return an HTTP status ( a `401` is fine ) and not a certificate error. `crictl` isn't installed on the node, which is why this uses `ctr`.

## Attach GPUs to bnenod05

Via the Proxmox UI:

1. Shut down bnenod05 first (hardware changes require the VM to be off).
2. Select bnenod05 → Hardware tab → Add → PCI Device.
3. Choose Raw Device, select 0a:00 from the dropdown (it'll show as the RTX 3090).
4. Tick All Functions — this passes through both the VGA (0a:00.0) and audio (0a:00.1) parts together as one unit
5. Tick PCI-Express — needed since we built the template on q35, which uses the PCIe bus (not legacy PCI).
6. Leave Primary GPU unticked — that's for cases where the VM needs the card for its own console/display; bnenod05 is headless and uses the regular Proxmox serial/VNC console, so the 3090s are purely compute devices here.
7. Click Add, then repeat the same for 0b:00 (the second card) as a second PCI Device entry.
8. Boot bnenod05.

After boot, inside the guest:

```
lspci -nnk | grep -i nvidia
nvidia-smi
```

`lspci` should show both cards now bound to the nvidia driver, and `nvidia-smi` should list both RTX 3090s with their VRAM.

## Attach a model storage disk to bnenod05

The second SSD on bnellm01 is there to hold LLM models; carve out a dedicated thin-provisioned disk to hold these models.

The disk will be mounted as `/mnt/models` on `bnenod05`.

The `local-path-provisioner` ansible role will provision the backends' PVCs ( ollama, sonar, tabbyapi ) here, one directory each.

Create the disk attached to the VM (shutdown the VM first):

```
root@bnellm01:~# qm list
      VMID NAME                       STATUS     MEM(MB)    BOOTDISK(GB) PID
       100 bnenod05                   running    98304             97.66 3828
      9000 tpl-ubuntu-kubernetes-node stopped    122880            97.66 0

root@bnellm01:~# qm set 100 --scsi1 data:1024   # 1TB, thin-provisioned - only consumes real pool space as it's written to
update VM 100: -scsi1 data:1024
  Logical volume "vm-100-disk-0" created.
  Logical volume data/vm-100-disk-0 changed.
scsi1: successfully created disk 'data:vm-100-disk-0,size=1T'
```

Then inside bnenod05:

```
knoxg@bnenod05:~$ lsblk  # confirm the new disk shows up
NAME   MAJ:MIN RM  SIZE RO TYPE MOUNTPOINTS
sda      8:0    0 97.7G  0 disk
├─sda1   8:1    0    1G  0 part /boot/efi
└─sda2   8:2    0 96.6G  0 part /var/lib/kubelet/pods/425b91c1-2ae5-4cae-be5d-8376735bde4c/volume-subpaths/tigera-ca-bundle/calico-node/7
                                /var/lib/kubelet/pods/e6dcb366-64dc-493d-ab3c-c1f0cbf2bcf0/volume-subpaths/tigera-ca-bundle/calico-typha/1
                                /
sdb      8:16   0    1T  0 disk

knoxg@bnenod05:~$ sudo parted /dev/sdb --script mklabel gpt mkpart primary ext4 0% 100%
knoxg@bnenod05:~$ sudo mkfs.ext4 /dev/sdb1
mke2fs 1.47.0 (5-Feb-2023)
Discarding device blocks: done
Creating filesystem with 268434944 4k blocks and 67108864 inodes
Filesystem UUID: 570ba723-ae5c-4abe-94bd-c1b63c903fe7
Superblock backups stored on blocks:
        32768, 98304, 163840, 229376, 294912, 819200, 884736, 1605632, 2654208,
        4096000, 7962624, 11239424, 20480000, 23887872, 71663616, 78675968,
        102400000, 214990848

Allocating group tables: done
Writing inode tables: done
Creating journal (262144 blocks): done
Writing superblocks and filesystem accounting information: done

knoxg@bnenod05:~$ sudo mkdir -p /mnt/models
knoxg@bnenod05:~$ sudo mount /dev/sdb1 /mnt/models

knoxg@bnenod05:~$ sudo blkid /dev/sdb1   # get the UUID for fstab
/dev/sdb1: UUID="570ba723-ae5c-4abe-94bd-c1b63c903fe7" BLOCK_SIZE="4096" TYPE="ext4" PARTLABEL="primary" PARTUUID="22899cf8-184d-43b9-99d8-253196dd0e10"

knoxg@bnenod05:~$ echo 'UUID=570ba723-ae5c-4abe-94bd-c1b63c903fe7  /mnt/models  ext4  defaults  0  2' | sudo tee -a /etc/fstab
UUID=570ba723-ae5c-4abe-94bd-c1b63c903fe7  /mnt/models  ext4  defaults  0  2

knoxg@bnenod05:~$ sudo mount -a
knoxg@bnenod05:~$ df -h /mnt/models
Filesystem      Size  Used Avail Use% Mounted on
/dev/sdb1      1007G   28K  956G   1% /mnt/models
```
