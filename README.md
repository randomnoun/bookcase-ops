
# bookcase-ops

**bookcase-ops** ( or DevSecBookcaseOps if you want to throw in a couple more buzz-syllables ) is a series of packer scripts and ansible tasks to maintain the handful of machines I have running in that bookcase over there.

It mostly exists because we're now using Kubernetes at work and I feel I now need to burden myself with configuring and running this on 
my home servers as well.

# What's in the box ?

Okay so what you've got is:

* setup - a few READMEs describing how it fits together and what you prerequisites you need installed
   * [SETUP-ARCHITECTURE.md](setup/SETUP-ARCHITECTURE.md) - bits and pieces
   * [SETUP-HARDWARE.md](setup/SETUP-HARDWARE.md) - what I'm running this on
   * [SETUP-ESX.md](setup/SETUP-ESX.md) - setting up the free version of ESX 6.0
* [packer-ubuntu-mysql](packer-ubuntu-mysql/) - a packer script to create a VM to run MySQL 8.0 and vault , on ubuntu 22
   * You'll need to create this first, as it will contain the vault server holding the secrets used in configuring kubernetes
   * [SETUP-VAULT.md](setup/SETUP-VAULT.md) - setting up vault
   * [SETUP-CERTIFICATE.md](setup/SETUP-CERTIFICATE.md) - setting up a CA and the site certificates used later in kubernetes ingresses
* [packer-ubuntu-kubernetes](packer-ubuntu-kubernetes/) - a packer script to create a kubernetes 1.25 cluster / API server running on ubuntu 22
* [packer-ubuntu-kubernetes-node](packer-ubuntu-kubernetes-node/) - a packer script to create a kubernetes 1.25 node running on ubuntu 22
* [ansible](ansible/README.md) - an ansible repository which deploys the following into kubernetes
   * nginx-ingress - to handle traffic into the cluster
   * democratic-csi - to handle storage for k8s pods ( connects to a TrueNAS SCALE box over NFS )
   * gitlab - a gitlab server
   * nexus2 - a nexus2 repository ( to hold java artifacts )
   * nexus3 - a nexus3 repository ( to hold docker artifacts )
   * xwiki - a wiki

The packer scripts are designed to install virtual machines in the free version of ESXi 6.0 server, but could probably be updated to other hosting environments easily enough.

Everything is hosted as subdomains of 'dev.randomnoun', which isn't a real TLD. So if you're copying any of this you may want to search and replace that to something else.

I'm also configuring the DNS and certificates externally to all this, guess I could virtualise that up as well if I'm feeling up to it. 

## License

bookcase-ops is licensed under a Simplified BSD License.
