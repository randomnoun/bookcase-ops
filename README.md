

# bookcase-ops

**bookcase-ops** ( or DevSecBookcaseOps if you want to throw in a couple more buzz-syllables ) is a series of packer scripts and ansible tasks to maintain the handful of machines I have running in that bookcase over there.

It mostly exists because we're now using Kubernetes at work and I feel I now need to burden myself with configuring and running this on my home servers as well.

Looking over my notes, this took about 4 weekends, spread over about 4 months (plus most of my Christmas/New Years downtime which I spent writing up all these READMEs), so hopefully this will save me / others having to go through all this again when I next have to upgrade all this high quality software product. Having said that, this is the ~~second~~ third time I've tried to build a kubernetes cluster onto bare VMs; if you haven't done that before then maybe budget another month or two on learning how all that supposedly works.

And you know, when I inevitably kick the bucket, my immediate family, who have difficulty selecting a different HDMI input or operating a printer, might be able to salvage some of my life's work. Which they won't be able to appreciate or comprehend in any way. I guess they could admire the punctuation or something. I have a niece starting computer science this year though, so I guess hope springs eternal.

**Update 2026-10-06:** OK so not only has my niece decided not to go ahead with that degree, but in the four years between starting this project and now, 
LLMs have become the shiny new thing that everyone is talking about. So I've gone out and dumped an outrageous amount of cash on an 'LLM server', which I'll be using to put myself out of a job,
if everything goes according to plan. I'm using proxmox on the LLM server as a hypervisor, and various bits and bobs on a k8s node running within that.

# What's in the box ?

Okay so what you've got is:

* setup - a few READMEs describing how it fits together and what prerequisites you need installed
   * [SETUP-ARCHITECTURE.md](setup/SETUP-ARCHITECTURE.md) - bits and pieces
   * [SETUP-HARDWARE.md](setup/SETUP-HARDWARE.md) - what I'm running this on
   * [SETUP-DNS.md](setup/SETUP-DNS.md) - DNS + DHCP configuration for the things in this project
   * [SETUP-NAS.md](setup/SETUP-NAS.md) - setting up the free version of TrueNAS SCALE
   * [SETUP-RUSTFS.md](setup/SETUP-RUSTFS.md) - setting up rustfs (s3-compatible storage provider) on the NAS
   * [SETUP-ESX.md](setup/SETUP-ESX.md) - setting up the free version of ESX 6.5; used this for the initial set of VMs, including the k8s API server and node
   * [SETUP-PROXMOX.md](setup/SETUP-PROXMOX.md) - setting up the free version of proxmox; used this for the k8s node that runs the LLM stuff
* [packer-ubuntu-mysql](packer-ubuntu-mysql/) - a packer script to create a VM to run vault, MySQL, postgres and clickhouse , on ubuntu 24
   * This'll need to be the first virtual machine you create, as it will contain the vault server holding the secrets used in configuring kubernetes
   * [SETUP-VAULT.md](setup/SETUP-VAULT.md) - setting up vault
   * [SETUP-CERTIFICATE.md](setup/SETUP-CERTIFICATE.md) - setting up a certificate authority (CA) and the site certificates used later in kubernetes ingresses
   * [SETUP-DATABASE.md](setup/SETUP-DATABASE.md) - setting up the databases, schemas, users, + a bunch of other application passwords and secrets   
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
   * wakapi - developer time tracker
* [SETUP-LLM.md](setup/SETUP-LLM.md) - the large language model server, node and containers. This is the index to the pages below
   * [SETUP-LLM-ARCHITECTURE.md](setup/SETUP-LLM-ARCHITECTURE.md) has a diagram of how it all fits together
   * added a proxmox variant of `packer-ubuntu-kubernetes-node`; creating the GPU node from it is in [SETUP-LLM-KUBERNETES.md](setup/SETUP-LLM-KUBERNETES.md)
   * Some middleware ( [SETUP-LLM-MIDDLEWARE.md](setup/SETUP-LLM-MIDDLEWARE.md) )
      * litellm - an LLM proxy
      * searxng - a search proxy for LLMs
      * langfuse - a log/observation thing for LLMs
   * Some backends ( [SETUP-LLM-BACKEND.md](setup/SETUP-LLM-BACKEND.md) )
      * ollama - an LLM engine
      * sonar - another one of those
      * tabbyapi - another one of those
   * Some frontends ( [SETUP-LLM-FRONTEND.md](setup/SETUP-LLM-FRONTEND.md) )
      * open-webui - a simple frontend to that ( connects to litellm )
      * pi - minimalistic terminal coding harness 
      * opencode - an "AI terminal" for interactive coding sessions
      * openhands - a "platform and control layer for repeatable coding agent workflows"
   
The packer scripts are designed to install most virtual machines in ESXi, but the k8s node VMs can now be either ESXi or proxmox.

Everything is hosted as subdomains of `.dev.randomnoun`, which isn't a real TLD. So if you're copying any of this you may want to search and replace that to something else.

I'm configuring the DNS and certificates manually ( see the SETUP docs above ). 

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
* [https://wakapi.dev.randomnoun](https://wakapi.dev.randomnoun)
* And the LLM-related stuff:
   * [https://litellm.dev.randomnoun](https://litellm.dev.randomnoun)
   * [https://langfuse.dev.randomnoun](https://langfuse.dev.randomnoun)
   * [https://searxng.dev.randomnoun](https://searxng.dev.randomnoun)
   * [https://open-webui.dev.randomnoun](https://open-webui.dev.randomnoun)
   * [https://opencode.dev.randomnoun](https://opencode.dev.randomnoun)
   * [https://openhands.dev.randomnoun](https://openhands.dev.randomnoun)

## Updates

* 2025-08: added commafeed
* 2025-10: added karakeep
* 2026-01: upgraded to k8s 1.35, ubuntu 24, calico 3.31. New k8s API server is bnekub03
* 2026-02: added atuin
* 2026-03: added wakapi
* 2026-08: added litellm, searxng
* 2026-10: added bnellm01, proxmox, bnenod05, ollama, open-webui, pi, opencode, openhands, sonar, tabbyapi, langfuse
   * Have gone a bit LLM-mad, will probably remove some of those as I find out what works and what doesn't

## License

bookcase-ops is licensed under a Simplified BSD License.
