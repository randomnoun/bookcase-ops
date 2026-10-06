
# packer-ubuntu-kubernetes-node

**packer-ubuntu-kubernetes-node**  is the node counterpart to **packer-ubuntu-kubernetes** 

So each kubernetes cluster has a single k8s API server ( although that can be spread across multiple hosts ), and many 'nodes' which run the applications that you're hosting in kubernetes.

The is the node bit.

It obtains the credentials to join the cluster from bnenas05 via ssh ( the credentials are copied there as part of the API server installation, but I'll probably move those to vault instead soon ). 

# Networking prerequisites

So yes, kubernetes has it's own networking layer, but you'll probably still want something to point to kubernetes. 
So you'll want to decide on a hostname for the server, and set up some DNS/DHCP rules to resolve it. 
I also hard-coded a MAC address, which you can generate [from here](https://dnschecker.org/mac-address-generator.php).

The hostname I'm using is `bnenod03` as it's in Brisbane ([BNE](https://www.iata.org/en/publications/directories/code-search/?airport.search=bne)) and this is the third time I've gone through this rigmarole.

See [SETUP-DNS.md](../setup/SETUP-DNS.md) on setting that up.

# Packer prerequisites

This version has been tested on packer 1.8.1 and has been updated to use the new, more complicated and arbitrarily different hcl format.

# Vault 

Most of the credentials are sourced from vault. 

Alternatively, you could use the 'simple' variant of these scripts, which puts all the credentials in a JSON file in the repository.

To disable vault lookups:

* copy the `simple-esxi-vars.json.sample` to `simple-esxi-vars.json` in the `src/main/packer/esxi` folder
* edit that file with the credentials you want to use. You'll probably want to change most of the entries in that json file. 
* edit the environment variables at the top of `build.sh` to contain: 

```
VARIANT=esxi
PACKER_VARS=simple-esxi-vars.json
PACKER_HCL=simple-esxi-ubuntu-kubernetes-node.pkr.hcl
WITH_VAULT=0
```

# Proxmox

To build the image on a Proxmox host instead of ESXi, use the proxmox variant of the scripts. Like the esxi variant, there's a vault-backed version (`proxmox-ubuntu-kubernetes-node.pkr.hcl`) and a simple-vars version (`simple-proxmox-ubuntu-kubernetes-node.pkr.hcl`).

To disable vault lookups:

* copy `simple-proxmox-vars.json.sample` to `simple-proxmox-vars.json` in the `src/main/packer/proxmox` folder
* edit that file with the credentials and host details for your proxmox server, in particular:
  * `proxmox_url` - the API URL for your proxmox host, e.g. `https://<host>:8006/api2/json`
  * `proxmox_username` / `proxmox_token` - an API token created under Datacenter > Permissions > API Tokens
  * `proxmox_node`, `proxmox_iso_storage_pool` (needs the `iso` content type, e.g. `local`), `proxmox_disk_storage_pool` / `proxmox_efi_storage_pool` (an lvm-thin or zfs pool capable of storing VM disks - check `pvesm status` on the node for the available pool names, e.g. `local-lvm` or `data`), `proxmox_bridge` (e.g. `vmbr0`)
* edit the environment variables at the top of `build.sh` to contain:

```
VARIANT=proxmox
PACKER_VARS=simple-proxmox-vars.json
PACKER_HCL=simple-proxmox-ubuntu-kubernetes-node.pkr.hcl
WITH_VAULT=0
```

To use vault instead, copy `proxmox-vars.json.sample` to `proxmox-vars.json` and set `PACKER_HCL=proxmox-ubuntu-kubernetes-node.pkr.hcl` / `WITH_VAULT=1`. Credentials are read from vault at `/secret/data/packer/proxmox/<proxmox_node>` (`username` and `token` fields), following the same pattern as the esxi vault secrets.

This variant builds the VM as `q35`/UEFI/`cpu_type=host`, and bakes in NVIDIA drivers + CUDA (`packer-scripts/02-install-nvidia.sh`), for use as a GPU-passthrough-ready Kubernetes node (e.g. for a host with RTX 3090s). The physical GPUs are deliberately **not** attached during the packer build — attach them to the cloned VM afterwards via the Proxmox UI/CLI, since passing them through while packer is provisioning would lock the cards.

# Creating the VM 

Then run the script.

```
./build.sh
```

# Layout

* `src/main/packer/common/` - files shared between all variants (cloud-init template, base install script, filesystem overlay)
* `src/main/packer/esxi/` - ESXi/vmware-iso builder, both vault-backed and simple-vars variants
* `src/main/packer/proxmox/` - Proxmox/proxmox-iso builder (simple-vars only for now)

# Variables

Variables are in [src/main/packer/esxi/esxi-vars.json](src/main/packer/esxi/esxi-vars.json)

# Notes

* Ubuntu version: 22.04.1 ( live server amd64 install )
* Kubernetes: 1.25.3
* Logs are written to `/opt/packer/packer-install.log` within the VM

