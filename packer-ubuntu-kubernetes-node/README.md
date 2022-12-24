
# packer-ubuntu-kubernetes-node

**packer-ubuntu-kubernetes-node**  is the node counterpart to **packer-ubuntu-kubernetes** 

So each kubernetes cluster has a single k8s API server ( although that can be spread across multiple hosts ), and many 'nodes' which run the applications that you're hosting in kubernetes.

The is the node bit.

It obtains the credentials to join the cluster from bnenas04 via ssh ( the credentials are copied there as part of the API server installation, but I'll probably move those to vault instead soon ). 

# Packer prerequisites

This version has been tested on packer 1.8.1 and has been updated to use the new, more complicated and arbitrarily different hcl format.

# Vault 

Most of the credentials are sourced from vault. 

Alternatively, you could use the 'simple' variant of these scripts, which puts all the credentials in a JSON file in the repository.

To enable that

* copy the `simple-vars.json.sample` to `simple-vars.json` in the `src/main/packer` folder
* edit that file with the credentials you want to use. You'll probably want to change most of the entries in that json file. 
* edit the environment variables at the top of `build.sh` to contain: 

```
PACKER_VARS=simple-vars.json
PACKER_HCL=simple-ubuntu-kubernetes-node.pkr.hcl
WITH_VAULT=0
```

# Creating the VM 

Then run the script.

```
./build.sh
```

# Variables

Variables are in [src/main/packer/vars.json](src/main/packer/vars.json)

# Notes

* Ubuntu version: 22.04.1 ( live server amd64 install )
* Kubernetes: 1.25.3
* Logs are written to `/opt/packer/packer-install.log` within the VM

