

# bookcase-ops

**bookcase-ops** ( or DevSecBookcaseOps if you want to throw in a couple more buzz-syllables ) is a series of packer scripts and ansible tasks to maintain the handful of machines I have running in that bookcase over there.

It mostly exists because we're now using Kubernetes at work and I feel I now need to burden myself with configuring and running this on my home servers as well.

Looking over my notes, this took about 4 weekends, spread over about 4 months (plus most of my Christmas/New Years downtime which I spent writing up all these READMEs), so hopefully this will save me / others having to go through all this again when I next have to upgrade all this high quality software product. Having said that, this is the ~~second~~ third time I've tried to build a kubernetes cluster onto bare VMs; if you haven't done that before then maybe budget another month or two on learning how all that supposedly works.

And you know, when I inevitably kick the bucket, my immediate family, who have difficulty selecting a different HDMI input or operating a printer, might be able to salvage some of my life's work. Which they won't be able to appreciate or comprehend in any way. I guess they could admire the punctuation or something. I have a niece starting computer science this year though, so I guess hope springs eternal.

# What's in the box ?

Okay so what you've got is:

* setup - a few READMEs describing how it fits together and what prerequisites you need installed
   * [SETUP-ARCHITECTURE.md](setup/SETUP-ARCHITECTURE.md) - bits and pieces
   * [SETUP-HARDWARE.md](setup/SETUP-HARDWARE.md) - what I'm running this on
   * [SETUP-DNS.md](setup/SETUP-DNS.md) - DNS + DHCP configuration for the things in this project
   * [SETUP-NAS.md](setup/SETUP-NAS.md) - setting up the free version of TrueNAS SCALE
   * [SETUP-ESX.md](setup/SETUP-ESX.md) - setting up the free version of ESX 6.5
* [packer-ubuntu-mysql](packer-ubuntu-mysql/) - a packer script to create a VM to run MySQL 8.0 and vault , on ubuntu 24
   * This'll need to be the first virtual machine you create, as it will contain the vault server holding the secrets used in configuring kubernetes
   * [SETUP-VAULT.md](setup/SETUP-VAULT.md) - setting up vault
   * [SETUP-CERTIFICATE.md](setup/SETUP-CERTIFICATE.md) - setting up a certificate authority (CA) and the site certificates used later in kubernetes ingresses
   * [SETUP-DATABASE.md](setup/SETUP-DATABASE.md) - setting up the db schema and user for xwiki   
* [SETUP-RESTIC.md](setup/SETUP-RESTIC.md) - setting up restic. This actually runs on the NAS, but we need to create certificates for it, so we're setting it up here after the CA has been created.
* [packer-ubuntu-kubernetes](packer-ubuntu-kubernetes/) - a packer script to create a kubernetes 1.35 cluster / API server running on ubuntu 24
* [packer-ubuntu-kubernetes-node](packer-ubuntu-kubernetes-node/) - a packer script to create a kubernetes 1.35 node running on ubuntu 24
* [ansible](ansible/README.md) - an ansible repository which deploys the following into kubernetes
   * nginx-ingress - to handle traffic into the cluster
   * democratic-csi - to handle storage for k8s pods ( connects to the TrueNAS SCALE box over NFS )
   * prometheus - monitoring
   * grafana - a dashboard for the statistics captured in prometheus ( once it's running, see [SETUP-GRAFANA.md](setup/SETUP-GRAFANA.md) )
   * gitlab - a gitlab server
   * nexus2 - a nexus2 repository ( to hold java artifacts )
   * nexus3 - a nexus3 repository ( to hold docker artifacts )
   * xwiki - a wiki
   * commafeed - an rss aggregator
   * karakeep - a bookmark organiser
   * atuin - shell history server

The packer scripts are designed to install virtual machines in the free version of ESXi 6.0 server, but could be used to deploy into other hosting environments easily enough.

Everything is hosted as subdomains of `.dev.randomnoun`, which isn't a real TLD. So if you're copying any of this you may want to search and replace that to something else.

I'm configuring the DNS and certificates manually ( see the SETUP docs above ). I guess I could virtualise that up as well if I'm feeling up to it. 

# Links to the things running in the cluster

Anyway once everything's running, you should be able to connect to the following in a browser:

* [https://wiki.dev.randomnoun](https://wiki.dev.randomnoun)
* [https://gitlab.dev.randomnoun](https://gitlab.dev.randomnoun)
* [https://nexus2.dev.randomnoun](https://nexus2.dev.randomnoun)
* [https://nexus3.dev.randomnoun](https://nexus3.dev.randomnoun)
* [https://prometheus.dev.randomnoun](https://prometheus.dev.randomnoun)
* [https://alertmanager.dev.randomnoun](https://alertmanager.dev.randomnoun)
* [https://grafana.dev.randomnoun](https://grafana.dev.randomnoun)
* [https://vault.dev.randomnoun](http://vault.dev.randomnoun)
* [http://vault.dev.randomnoun:8200](http://vault.dev.randomnoun:8200) (HTTP port)
* [https://commafeed.dev.randomnoun](https://commafeed.dev.randomnoun)
* [https://karakeep.dev.randomnoun](https://karakeep.dev.randomnoun)
* [https://atuin.dev.randomnoun](https://atuin.dev.randomnoun)

## Updates

* 2026-02: added atuin
* 2026-01: upgraded to k8s 1.35, ubuntu 24, calico 3.31. New k8s API server is bnekub03
* 2025-10: added karakeep
* 2025-08: added commafeed

## License

bookcase-ops is licensed under a Simplified BSD License.
