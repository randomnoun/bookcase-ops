# bookcase-ops restic

This bit describes how to install and configure a [restic](https://restic.net/) REST server on the NAS. 

Restic is an open-source backup client/server with a number of different storage engines; in this document we'll be creating a REST server on `bnenas04`.

The server will have the domain name `restic.dev.randomnoun` (which was created in [SETUP-DNS.md](SETUP-DNS.md) ), with traffic secured over TLS.

## Create the site certificates 

You'll need to have the openssl-ca scripts installed first, as described in [SETUP-CERTIFICATE.md](SETUP-CERTIFICATE.md).

On the host containing those scripts, create another certificate and copy that to the NAS. 

We could store this in vault as well, but we won't be needing them there as we're not running restic from within our kubernetes cluster.

```
# create the restic certificate
cd /opt/openssl-ca
./bin/create-certificate.sh restic.dev.randomnoun

# copy the restic certificate and key to the nas, as that's where the restic server will run
ssh knoxg@bnenas04.dev.randomnoun 'mkdir -p /mnt/raidvolume/compressed/truenas/restic/tls'
scp private/restic.dev.randomnoun-key.pem knoxg@bnenas04.dev.randomnoun:/mnt/raidvolume/compressed/truenas/restic/tls/restic.dev.randomnoun-key.pem
scp cert/restic.dev.randomnoun.pem        knoxg@bnenas04.dev.randomnoun:/mnt/raidvolume/compressed/truenas/restic/tls/restic.dev.randomnoun.pem
```

# Installation

We're using [`restic/rest-server`](https://hub.docker.com/r/restic/rest-server) docker image from [dockerhub](https://hub.docker.com/r/restic/rest-server) to run the server.

Select the 'Apps' link on the left hand side of the TrueNAS control panel.

Then select the 'Available Applications' tab, and click the 'Launch Docker Image' button ( that button is available on all tabs on that page, but it only appears to work on that tab ).

In step 1 (Application Name), enter the application name `restic`

![](image/restic-1.png)

In step 2 (Container Images), enter the image repository `restic/rest-server`, and leave the other fields as-is.

![](image/restic-2.png)

Skip step 3 (Container Entrypoint)

In step 4 (Container Environment Variables), create an environment variable `OPTIONS` with the value 
`--listen :9000 --tls --tls-cert /tls/restic.dev.randomnoun.pem --tls-key  /tls/restic.dev.randomnoun-key.pem`

You're using port 9000 rather than the default port 8000, as TrueNAS won't let you open a port lower than 9000 in step 6.

Also note those paths to the TLS certificates are paths within the restic container, you'll map those to paths on the NAS in step 7 later.

![](image/restic-3.png)

Skip step 5 (Networking)

In step 6 (Port Forwarding), click 'Add' and add a mapping from port 9000 on the host to port 9000 in the container.

![](image/restic-6.png)

In step 7 (Storage), you'll create two host path volumes:

* The first volume lets the container access the TLS certificates copied to the nas earlier, and can be set as read-only
   * Host path: `/mnt/raidvolume/compressed/truenas/restic/tls`
   * Mount path: `/tls`
   * Read only: checked
* The second volume will stores the backup repositories, and must be writable
   * Host path: `/mnt/raidvolume/compressed/backup/restic`
   * Mount path `/data`

![](image/restic-7.png)

Skip step 8 (Workload Details)

Skip step 9 (Scaling/Upgrade Policy)

Skip step 10 (Resource Reservation)

Skip step 11 (Resource Limits)

Skip step 12 (Portal Configuration)

Step 13 (Confirm Options) should look a bit like this:

![](image/restic-13.png)

Once you click save, you should eventuall see a restic tile appear on the 'Installed Applications' tab, which will eventually show the status of 'ACTIVE':

![](image/restic-installed.png)

# Creating a user

Once restic is up and running, you can create a user from the commandline on `bnenas04`. 

You can run `sudo docker ps` to get a list of docker containers; from here you might be surprised by the number of docker containers with "k8s" in their names.

So it turns out that TrueNAS manages its docker containers using `k3s` ( a cut-down version of kubernetes ), which is sort of interesting considering the whole point of this setup was to create a kubernetes cluster. So just think of this one as another completely different 1-machine cluster, and ignore it for the purposes of the cluster we're setting up later.

The docker container we're interested in is the 'restic' one, and it's not the one running the `/pause` command.

```
knoxg@bnenas04:/$ sudo docker ps | grep k8s_ix-chart_restic
[sudo] password for knoxg:

ab164af7889a   4860e044dfed                 "/entrypoint.sh"         23 minutes ago   Up 22 minutes             k8s_ix-chart_restic-ix-chart-5459b6f4f4-4d9l5_ix-restic_70702a70-
```

We need the container ID, which is the first set of hexadecimal characters at the start of the container listing.

```
knoxg@bnenas04:/$ sudo docker ps | grep k8s_ix-chart_restic | cut -d' ' -f1

ab164af7889a
```

Once we've got that, we can call the `create_user` script to create a new restic user. 

Here I'm creating a user with the username `bnehyp02` and the password `KlMUE62Y887q`,
as I'm planning to have a separate username from each server that will be backing things up to restic.

You probably want to add the username and password to vault, but that's up to you.

```
knoxg@bnenas04:/$ sudo docker exec -it $(sudo docker ps | grep k8s_ix-chart_restic | cut -d' ' -f1) create_user bnehyp02 KlMUE62Y887q
Adding password for user bnehyp02
```

So now you should be able to connect to that restic server using that username and password.






