
# ansible

This is the ansible part of bookcase-ops which deploys a few system-related containers into kubernetes, and a handful of applications.

If running this from Windows, you will need to run from WSL ( Windows Subsystem for Linux ); the last time I looked, ansible + helm didn't work all that well from Windows directly.

So you'll need to install [ansible](https://docs.ansible.com/ansible/latest/installation_guide/intro_installation.html), [helm](https://helm.sh/docs/intro/install/) and probably some [ansible galaxy](https://docs.ansible.com/ansible/latest/collections_guide/collections_installing.html) collections. I didn't come up with these names.

## Certificates

Since I use a non-standard `.randomnoun` top level domain on my dev machines, I have to create my own certificates using my own certificate authority (CA), rather
than using letsencrypt. If I find an easier way of doing this in future, I'll update this project.

Setting up the certificates and the various TLS certs are covered in [SETUP-CERTIFICATE.md](../setup/SETUP-CERTIFICATE.md)


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

Anyway to install these two components into the kubernetes cluster, run:   

```
. vault-login.sh
ansible-playbook -vvv -e deployments=bnekub02 k8s_system_bnekub02.yml
```

To verify it's up, run

```
knoxg@bnekub02:~$ kubectl -n nginx-ingress get daemonset
NAME            DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE   NODE SELECTOR   AGE
nginx-ingress   1         1         0       1            0           <none>          2s

knoxg@bnekub02:~$ kubectl -n nginx-ingress get pods
NAME                  READY   STATUS    RESTARTS   AGE
nginx-ingress-s6rqh   1/1     Running   0          37s
```

and when it doesn't start up properly, well, that's what google's for.

## Applications

The initial set of applications are:

* gitlab
* xwiki
* nexus2
* nexus3

what I would suggest you do is to comment out all but one of those in k8s_apps_bnekub02.yml and deploy a single application, and then uncomment the rest as you get those up and running. 

To install this stuff, run

```
. vault-login.sh
ansible-playbook -vvv -e deployments=bnekub02 k8s_apps_bnekub02.yml
```
 
And to check it's running:

```
knoxg@bnekub02:~$ kubectl -n dev-xwiki get deployments
NAME    READY   UP-TO-DATE   AVAILABLE   AGE
xwiki   1/1     1            1           5m53s
```

Use this sort of thing to scale it down

```
knoxg@bnekub02:~$ kubectl -n dev-xwiki scale deployments/xwiki --replicas=0
deployment.apps/xwiki scaled
```






