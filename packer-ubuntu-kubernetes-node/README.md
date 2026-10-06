
# packer-ubuntu-kubernetes-node

**packer-ubuntu-kubernetes-node**  is the node counterpart to **packer-ubuntu-kubernetes** 

So each kubernetes cluster has a single k8s API server ( although that can be spread across multiple hosts ), and many 'nodes' which run the applications that you're hosting in kubernetes.

The is the node bit.

It obtains the credentials to join the cluster from bnenas05 via ssh ( the credentials are copied there as part of the API server installation, but I'll probably move those to vault instead soon ). 

# Networking prerequisites

So yes, kubernetes has it's own networking layer, but you'll probably still want something to point to kubernetes. 
So you'll want to decide on a hostname for the server, and set up some DNS/DHCP rules to resolve it. 
I also hard-coded a MAC address, which you can generate [from here](https://dnschecker.org/mac-address-generator.php).

The hostname I'm using is `bnenod04` as it's in Brisbane ([BNE](https://www.iata.org/en/publications/directories/code-search/?airport.search=bne)) and this is the fourth time I've gone through this rigmarole.

I've now also got a `bnenod05` which is going to be used for GPU-heavy LLM tasks.

See [SETUP-DNS.md](../setup/SETUP-DNS.md) on setting those up.

# Packer prerequisites

This version has been tested on packer 1.8.1 and has been updated to use the new, more complicated and arbitrarily different hcl format.

# Vault 

Most of the credentials are sourced from vault. 

Alternatively, you could use the 'simple' variant of these scripts, which puts all the credentials in a JSON file in the repository.

To disable vault lookups:

* copy the `simple-esxi-vars.json.sample` to `simple-esxi-vars.json` in the `src/main/packer/esxi` folder
* copy the `simple-proxmox-vars.json.sample` to `simple-proxmox-vars.json` in the `src/main/packer/proxmox` folder
* edit those files with the credentials you want to use. You'll probably want to change most of the entries in that json file. 
* edit `build.sh` and set the `WITH_VAULT` environment variables to `0`

# ESXi vs proxmox

The main kubernetes API server runs on ESXi, but the nodes can run on either ESXi or proxmox. 

I'm using proxmox for the node running with access to the GPUs, so the scripts are a bit more specialised to that use-case.  

The first arguemnt to `build.sh` script must be either `esxi` or `proxmox`, which will dictate which hypervisor we are deploying to.

* If 'esxi', will create a VM called 'bnenod04'
* If 'proxmox', will create a VM template called 'tpl-ubuntu-kubernetes-node' ( VMID 9000 ), which will be used to create the 'bnenod05' VM
   * This variant builds the VM as `q35`/UEFI/`cpu_type=host`, and bakes in NVIDIA drivers + CUDA (`packer-scripts/02-install-nvidia.sh`), for use as a GPU-passthrough-ready Kubernetes node. 
   * The physical GPUs are deliberately **not** attached during the packer build since passing them through while packer is provisioning would lock the cards. Attach them to the cloned VM afterwards via the Proxmox UI/CLI.  

# Creating the VM 

Then run the script.

```
./build.sh
```

# Joining the kubernetes cluster

In both cases, the first thing you should do in the new VM once it's running is to rename the host ( if required ), then join the kubernetes cluster, by running

```
sudo /opt/backup/join-command/kubernetes-join-command.sh
```

# Layout

* `src/main/packer/common/` - files shared between all variants (cloud-init template, base install script, filesystem overlay)
* `src/main/packer/esxi/` - ESXi/vmware-iso builder, both vault-backed and simple-vars variants
* `src/main/packer/proxmox/` - Proxmox/proxmox-iso builder, both vault-backed and simple-vars variants

# Variables

Variables are in [src/main/packer/esxi/esxi-vars.json](src/main/packer/esxi/esxi-vars.json)

# Notes

* Ubuntu version: 22.04.1 ( live server amd64 install )
* Kubernetes: 1.25.3
* Logs are written to `/opt/packer/packer-install.log` within the VM

