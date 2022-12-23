
# packer-ubuntu-kubernetes

**packer-ubuntu-kubernetes**  is a project to create a non-production [Kubernetes](https://kubernetes.io/) API server VM 
on [ESX](https://www.vmware.com/au/products/esxi-and-esx.html) 6.0 
using [packer](https://www.packer.io/).  

I'm running kubernetes on a VM because performing the same tasks as an OS scheduler requires an entire machine or three these days.

And it's non-production because I'm running this at home with a [TrueNAS SCALE](https://www.truenas.com/truenas-scale/) box providing storage, 
instead of paying Amazon/Microsoft/Google several hundred thousand dollars to do that for me in the cloud.

I guess I could use [minikube](https://minikube.sigs.k8s.io/docs/start/), or [k3s](https://k3s.io/) or the 
[kubernetes that's now integrated into Docker Desktop](https://docs.docker.com/desktop/kubernetes/), but why would you
want to use a version of kubernetes that's less complicated than the kubernetes you're going to have to deal with once it's 
actually deployed into the cloud somewhere.

This kubernetes installation uses [calico](https://projectcalico.docs.tigera.io/getting-started/kubernetes/) to provide networking, 
and [democratic-csi](https://github.com/democratic-csi/democratic-csi) for storage. The democratic-csi bits aren't in this project.

# Networking prerequisites

So yes, kubernetes has it's own networking layer, but you'll probably still want something to point to kubernetes. 
So you'll want to decide on a hostname for the server, and set up some DNS/DHCP rules to resolve it. 
I also hard-coded a MAC address, which you can generate [from here](https://dnschecker.org/mac-address-generator.php).

# ESX prerequisites

This project has been tested on ESX 6.0. 

You'll need to [jump through a few hoops](../setup/SETUP-ESX.md) to get packer to be able to create virtual machines on it.

Most of that was copied from [Nick Charlton's page here](https://nickcharlton.net/posts/using-packer-esxi-6.html), but I'm including 
the steps in this project in case that second link bitrots.

# Packer prerequisites

This project has been tested on packer 1.8.1 and has been updated to use the new, more complicated and arbitrarily different hcl format.

# Vault prerequisites

Well, you're not supposed to put passwords or other credentials in github, and installing kubernetes is an exercise in wrangling passwords so 
what you'll want to do is to create another VM that runs [hashicorp vault](https://www.vaultproject.io/), 
and use that to stash all your credentials. 

So [here's some steps](../setup/SETUP-VAULT.md) on getting that up and running.

Once vault is running, 
* copy `vault-login.sh.sample` to `vault-login.sh`
* update the `role_id` and `secret_id` to the real role id and secret id that packer will be using to access those credentials

# Alternatively 

Alternatively, you could use the 'simple' variant of these scripts, which puts all the credentials in a JSON file in the repository.

To enable that

* copy the `simple-vars.json.sample` to `simple-vars.json` in the `src/main/packer` folder
* edit that file with the credentials you want to use. You'll probably want to change most of the entries in that json file. 
* edit the environment variables at the top of `build.sh` to contain: 

```
PACKER_VARS=simple-vars.json
PACKER_HCL=simple-ubuntu-kubernetes.pkr.hcl
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
* Logs are written to `/opt/packer/packer-install-log.txt` within the VM

