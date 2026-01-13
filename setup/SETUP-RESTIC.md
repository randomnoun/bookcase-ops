# bookcase-ops restic

This bit describes how to install and configure a [restic](https://restic.net/) REST server on the NAS. 

Restic is an open-source backup client/server with a number of different storage engines; in this document we'll be creating a REST server on `bnenas05`.

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
ssh knoxg@bnenas05.dev.randomnoun 'mkdir -p /mnt/raidvolume/compressed/bookcase-ops/restic/tls'
scp private/restic.dev.randomnoun-key.pem knoxg@bnenas05.dev.randomnoun:/mnt/raidvolume/compressed/bookcase-ops/restic/tls/restic.dev.randomnoun-key.pem
scp cert/restic.dev.randomnoun.pem        knoxg@bnenas05.dev.randomnoun:/mnt/raidvolume/compressed/bookcase-ops/restic/tls/restic.dev.randomnoun.pem
```

# Installation

We're using [`restic/rest-server`](https://hub.docker.com/r/restic/rest-server) docker image from [dockerhub](https://hub.docker.com/r/restic/rest-server) to run the server.

Select the 'Apps' link on the left hand side of the TrueNAS control panel.

Then select the 'Available Applications' tab, and click the 'Launch Docker Image' button ( that button is available on all tabs on that page, but it only appears to work on that tab ).

### Application name

In step 1 (Application Name), enter the application name `restic`

![](image/restic-1.png)

### Container images

In step 2 (Container Images), enter the image repository `restic/rest-server`, and leave the other fields as-is.

![](image/restic-2.png)

### Container entrypoint

Skip step 3 (Container Entrypoint)

### Container environment variables

In step 4 (Container Environment Variables), create an environment variable `OPTIONS` with the value 
`--listen :9060 --tls --tls-cert /tls/restic.dev.randomnoun.pem --tls-key  /tls/restic.dev.randomnoun-key.pem`

You're using port 9060 rather than the default port 8000, as TrueNAS won't let you open a port lower than 9000 in step 6.

Also note those paths to the TLS certificates are paths within the restic container, you'll map those to paths on the NAS in step 7 later.

![](image/restic-4.png)

### Networking

Skip step 5 (Networking)

### Port forwarding

In step 6 (Port Forwarding), click 'Add' and add a mapping from port 9060 on the host to port 9060 in the container.

![](image/restic-6.png)

### Storage

In step 7 (Storage), you'll create two host path volumes:

* The first volume lets the container access the TLS certificates copied to the nas earlier, and can be set as read-only
   * Host path: `/mnt/raidvolume/compressed/truenas/restic/tls`
   * Mount path: `/tls`
   * Read only: checked
* The second volume will stores the backup repositories, and must be writable
   * Host path: `/mnt/raidvolume/compressed/backup/restic`
   * Mount path `/data`

![](image/restic-7.png)

### Workload details

Skip step 8 (Workload Details)

### Scaling/upgrade policy

Skip step 9 (Scaling/Upgrade Policy)

### Resource reservation

Skip step 10 (Resource Reservation)

### Resource limits

Skip step 11 (Resource Limits)

### Portal configuration

Skip step 12 (Portal Configuration)

### Confirm options

Step 13 (Confirm Options) should look a bit like this:

![](image/restic-13.png)

Once you click save, you should eventuall see a restic tile appear on the 'Installed Applications' tab, which will eventually show the status of 'ACTIVE':

![](image/restic-installed.png)

# Creating a user

Once restic is up and running, you can create a user from the commandline on `bnenas05`. 

The docker container we're interested in is the 'restic' one, and it's not the one running the `/pause` command.

```
knoxg@bnenas05:~$ sudo docker ps | grep restic
aacfdecdfa4e   restic/rest-server:0.14.0                  "/entrypoint.sh"         2 minutes ago        Up 2 minutes (healthy)        8000/tcp, 0.0.0.0:9060->9060/tcp, :::9060->9060/tcp                                    ix-restic-restic-server-1
```

We need the container ID, which is the first set of hexadecimal characters at the start of the container listing.

```
knoxg@bnenas05:/$ sudo docker ps | grep restic | cut -d' ' -f1

aacfdecdfa4e
```

Once we've got that, we can call the `create_user` script to create a new restic user. 

Here I'm creating a user with the username `bnehyp02` and the password `KlMUE62Y887q`,
as I'm planning to have a separate username from each server that will be backing things up to restic.

You probably want to add the username and password to vault, but that's up to you.

```
knoxg@bnenas05:/$ sudo docker exec -it $(sudo docker ps | grep restic | cut -d' ' -f1) create_user bnehyp02 KlMUE62Y887q
Adding password for user bnehyp02
```

So now you should be able to connect to that restic server using that username and password.

# Creating backups with restic

So from bnehyp02, copy the CA certificate and install it in the trusted certificate store:

```
sudo scp knoxg@bnenas05.dev.randomnoun:/mnt/raidvolume/compressed/bookcase-ops/ca/cacert.pem /usr/local/share/ca-certificates/cacert.crt
sudo update-ca-certificates
# check the certificate is recognised
awk -v cmd='openssl x509 -noout -subject' '/BEGIN/{close(cmd)};{print | cmd}' < /etc/ssl/certs/ca-certificates.crt | grep randomnoun
```

And install the restic client

```
sudo apt-get install restic
```

And create a restic repository for that host on the nas

```
# the repository URL contains the username and user password
export RESTIC_REPOSITORY=rest:https://bnehyp02:KlMUE62Y887q@restic.dev.randomnoun:9060/bnehyp02/

# this will prompt for a repository password
restic init
```

Backup the DHCP and DNS configuration folders

```
# this fails with some permission denied errors
restic backup --one-file-system /etc/bind /etc/dhcp

# probably want this instead
sudo restic -r ${RESTIC_REPOSITORY} backup --one-file-system /etc/bind /etc/dhcp
```

