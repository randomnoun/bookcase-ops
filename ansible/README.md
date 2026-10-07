
# ansible

This is the ansible part of bookcase-ops which deploys a few system-related containers into kubernetes, and a handful of applications.

If running this from Windows, you will need to run from WSL ( Windows Subsystem for Linux ); the last time I looked, ansible + helm didn't work all that well from Windows directly.

So you'll need to install [ansible](https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html), [helm](https://helm.sh/docs/intro/install/) and probably some [ansible galaxy](https://docs.ansible.com/ansible/latest/collections_guide/collections_installing.html) collections. I didn't come up with these names.

## Certificates

Since I use a non-standard `.randomnoun` top level domain on my dev machines, I have to create my own certificates using my own certificate authority (CA), rather
than using letsencrypt. If I find an easier way of doing this in future, I'll update this project.

Setting up the certificates and the various TLS certs are covered in [SETUP-CERTIFICATE.md](../setup/SETUP-CERTIFICATE.md)

## Helm repositories

We need a few helm repositories and plugins:

```
helm repo add democratic-csi https://democratic-csi.github.io/charts/
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
helm repo update

helm plugin install https://github.com/databus23/helm-diff
```

## Node labels

One more complication is that some deployments must run on node with a GPU; I'll be labelling the bnenod05 node ( running on bnellm01 ) with a couple of labels to help with scheduling:

* gpu-vendor=nvidia
* gpu-model=rtx-3090

with the intention of labelling other nodes with other vendors/models once this all becomes obsolete in a year or two.

## System components

The system components installed are:

* **kubernetes-democratic-csi**
   * CSI stands for 'container storage interface', which is a way kubernetes abstracts away filesystems, which you'd think was already a good enough abstraction for most people.
   * `democratic-csi` is an [open source project](https://github.com/democratic-csi/democratic-csi) providing CSI drivers for for freenas ( now named TrueNAS ),  so you can do things like provision PVCs ( persistent volume claims ) on the nas.  
* **kubernetes-nginx-ingress**
   * an ingress is how you get network traffic into you applications in kubernetes. This one uses [nginx](https://nginx.org/en/).
   * There appears to be two flavours of nginx ingresses: one that uses [`nginx.ingress.*` annotations](https://kubernetes.github.io/ingress-nginx/user-guide/nginx-configuration/annotations/), and one that uses [`org.nginx.*` annotations](https://docs.nginx.com/nginx-ingress-controller/configuration/ingress-resources/advanced-configuration-with-annotations/).  
     Our ingresses use `org.nginx.*` annotations.
   * It doesn't use the helm-installed nginx ingress either, because that won't work unless you have a real loadbalancer, unless you install something called [MetalLB](https://metallb.universe.tf/) which [doesn't work either](https://metallb.universe.tf/configuration/calico/).
* **prometheus**
   * prometheus is used for event monitoring and alerting. It's a time series database containing metrics scraped from the other containers in the cluster.
   * there's also an 'alertmanager' component which presumably manages alerts.
* **grafana**
   * gives you a nicer frontend to the metrics stored in prometheus
* **k8s-node-labels**
   * applies the GPU node labels described above (`gpu-vendor`, `gpu-model`) to whichever nodes need them. Generic/data-driven so new nodes/vendors just need an entry in `vars/k8s-node-labels/bnekub03.vars.yml`, not a new role.
* **nvidia-device-plugin**
   * advertises `nvidia.com/gpu` as a schedulable resource for any node labelled `gpu-vendor=nvidia`, and registers an `nvidia` RuntimeClass so pods (including the plugin itself) actually run under containerd's nvidia runtime rather than plain `runc`.
   * the chart's own default node affinity expects Node Feature Discovery labels we don't run; it's overridden off (see the comments in `nvidia-device-plugin-helm-values.j2.yml`)
* **local-path-provisioner**
   * [Rancher's local-path-provisioner](https://github.com/rancher/local-path-provisioner), giving a `bnenod05-local-path` StorageClass backed by local disk on bnenod05 rather than NFS. 
   * Used for things like ollama's model storage, which is pinned to a node with dedicated GPU and storage; a local disk avoids the latency of loading large model files over the network.

Arguably `prometheus` and `grafana` should have been installed with the other applications below, but hey.

Anyway to install these components into the kubernetes cluster, run:   

```
. vault-login.sh
ansible-playbook -vvv -e deployments=bnekub03 k8s_system_bnekub03.yml
```

To verify it's up, run

```
knoxg@bnekub03:~$ kubectl -n nginx-ingress get daemonset
NAME            DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE   NODE SELECTOR   AGE
nginx-ingress   1         1         0       1            0           <none>          2s

knoxg@bnekub03:~$ kubectl -n nginx-ingress get pods
NAME                  READY   STATUS    RESTARTS   AGE
nginx-ingress-s6rqh   1/1     Running   0          37s
```

and when it doesn't start up properly, well, that's what google's for.

## Applications

The initial set of applications are:

* gitlab
* nexus2
* nexus3
* xwiki
* commafeed
* karakeep
* atuin
* wakapi
* litellm
* ollama
* open-webui
* searxng

what I would suggest you do is to restrict to a specific app using `-e app=xxxxx` (see cmdline below), and deploy a single application at a time, 
fixing up the startup failures as you go.

### Why both nexus2 and nexu3 ?

You could probably just get by with nexus3, as it can hold maven artifacts just fine, but I'm using nexus2 for the same reason that sonatype still use nexus2 for maven central. Which is that nexus3, whilst admirably reinventing quite a lot of wheels, doesn't seem to have reached feature parity with nexus2 for maven repositories just yet.

### Here be dragons 

Once you get down to the LLM-looking containers, head over to [SETUP-LLM.md](../setup/SETUP-LLM.md) because it's all a bit fiddly and things need to be created
and configured in the right order.  

## Installation

To install this stuff, run

```
. vault-login.sh
ansible-playbook -vvv -e deployments=bnekub03 k8s_apps_bnekub03.yml
```

Or to just install a single app, run

```
. vault-login.sh
ansible-playbook -vvv -e deployments=bnekub03 -e app=atuin k8s_apps_bnekub03.yml
```

 
And to check it's running:

```
knoxg@bnekub03:~$ kubectl -n dev-xwiki get deployments
NAME    READY   UP-TO-DATE   AVAILABLE   AGE
xwiki   1/1     1            1           5m53s
```

Use this sort of thing to scale it down

```
knoxg@bnekub03:~$ kubectl -n dev-xwiki scale deployments/xwiki --replicas=0
deployment.apps/xwiki scaled
```






