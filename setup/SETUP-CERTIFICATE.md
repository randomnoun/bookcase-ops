# Setting up certificates

Kubernetes and docker seem to work better when everything's running on HTTPS, so you'll need to create some certificates to encrypt traffic to your kubernetes applications.

Because I don't want this stuff accidentally ending up on the internet, I use a private top-level domain (TLD) of `.randomnoun`, which isn't a real TLD. 
You might want to choose something else. Probably not `verisign` though, as people might confuse that with verisign. Which is completely different.

## Create a certificate authority (CA)

First off, you'll need a certificate authority (CA). 

A CA is a special type of certificate that lets your browser recognise and trust domains 'signed' by that certificate. 
You've probably got a few dozen CAs already installed in your browser, this will be one more.

You probably want to create your CA (and the key for that CA) on a relatively secure host. I'm using the same virtual machine I've running **vault** on. 

Run the following

```
# check out the openssl bits of this project and install them in /opt/openssl-ca
mkdir src
cd src
git clone --no-checkout https://github.com/randomnoun/bookcase-ops.git bookcase-ops
cd bookcase-ops
git sparse-checkout set openssl-ca
git checkout
chmod a+x openssl-ca/bin/*
openssl-ca/bin/init-openssl-ca.sh
```

Then create the initial CA certificate used to sign all the other ones.

```
# create the initial CA certificate
cd /opt/openssl-ca
./bin/create-ca.sh
```

The [`.bin/create-ca.sh`](../openssl-ca/bin/create-ca.sh) command will prompt for country, state, locality, organisation, common name, and email address. 

Some sample output and answers::

```
You are about to be asked to enter information that will be incorporated
into your certificate request.
What you are about to enter is what is called a Distinguished Name or a DN.
There are quite a few fields but you can leave some blank
For some fields there will be a default value,
If you enter '.', the field will be left blank.
-----
Country Name (2 letter code) [AU]:AU
State or Province Name (full name) [Some-State]:QLD
Locality Name (eg, city) []:Brisbane
Organization Name (eg, company) [Internet Widgits Pty Ltd]:randomnoun
Organizational Unit Name (eg, section) []:
Common Name (e.g. server FQDN or YOUR name) []:randomnoun
Email Address []:knoxg+cacert@randomnoun.com
```

Now your have a CA certificate, you can check the certificate has been installed on this host via:

```
# check the certificate is recognised
awk -v cmd='openssl x509 -noout -subject' '/BEGIN/{close(cmd)};{print | cmd}' < /etc/ssl/certs/ca-certificates.crt | grep randomnoun
```

You might want to copy the `/opt/openssl-ca/ca/cacert.crt` certificate to your local machine (or any other machine that will be accessing these dev sites),
and add them to your browser's certificate store, otherwise you're going to get warnings about untrusted certificates.

## Create the site certificates 

For each site certificate, you will need to:

* Generate a private key
* Generate a certificate signing request (csr)
* Generate the certificate extensions (ext)
* Generate a TLS certificate for the site, signed by the CA.

The [`./bin/create-certificate.sh`](../openssl-ca/bin/create-certificate.sh) script will perform these tasks, but you might want to run through it manually the first time to make sure it's doing what you think it should be doing.

You may want to also want change the SUBJECT variable ( `/C=AU...` ) at the top of the script to match your country, state, locality and organisation. 
The common name (CN) component must remain the web domain of the certificate, which is supplied as the first parameter of the script.
( e.g. `./bin/create-certificate.sh gitlab.dev.randomnoun` )

To create all the certificates for the applications in bookcase-ops, run:

```
cd /opt/openssl-ca
./bin/create-certificate.sh wiki.dev.randomnoun
./bin/create-certificate.sh gitlab.dev.randomnoun
./bin/create-certificate.sh nexus2.dev.randomnoun
./bin/create-certificate.sh nexus3.dev.randomnoun
./bin/create-certificate.sh docker-snapshots.nexus3.dev.randomnoun
./bin/create-certificate.sh docker-releases.nexus3.dev.randomnoun
./bin/create-certificate.sh docker-combined.nexus3.dev.randomnoun
```

So a few notes:
* The wiki is hosted on `wiki.dev.randomnoun`, even though strictly speaking, it's an xwiki wiki.
* There are 4 domains registered for `nexus3.dev.randomnoun`, one for the main UI and three for the docker repositories hosted in nexus3.
   * `docker-snapshots` contains development builds
   * `docker-releases` contains release builds
   * `docker-combined` is a proxy repository which can be used to access either snapshots or releases using a single repository.

## Upload to the vault

Each site's key and certificate are then uploaded to vault, using the `./bin/upload-certificate.sh` script.  

To upload all the certificates for the applications in bookcase-ops, run:

```
# need to be logged into vault first
vault login
./bin/upload-certificate.sh wiki.dev.randomnoun     k8s/bnekub02/secret/dev-xwiki/xwiki-tls-secret
./bin/upload-certificate.sh gitlab.dev.randomnoun   k8s/bnekub02/secret/dev-gitlab/gitlab-tls-secret
./bin/upload-certificate.sh nexus2.dev.randomnoun   k8s/bnekub02/secret/dev-nexus2/nexus2-tls-secret
./bin/upload-certificate.sh nexus3.dev.randomnoun   k8s/bnekub02/secret/dev-nexus3/nexus3-tls-secret
./bin/upload-certificate.sh docker-snapshots.nexus3.dev.randomnoun k8s/bnekub02/secret/dev-nexus3/docker-snapshots-tls-secret
./bin/upload-certificate.sh docker-releases.nexus3.dev.randomnoun  k8s/bnekub02/secret/dev-nexus3/docker-releases-tls-secret
./bin/upload-certificate.sh docker-combined.nexus3.dev.randomnoun  k8s/bnekub02/secret/dev-nexus3/docker-combined-tls-secret
```

## Create an ansible approle and long-lived auth token:

Ansible will need a approle within vault in order to read these secrets. 

To create that, run the following:

```
cd ~
vault policy write ansible-read-policy - << EOF
# ansible-read-policy
#
# gives read access to the secrets under 'k8s', and 'db', including
#   k8s/bnekub02/storageclass
#   k8s/bnekub02/secret
#   db/bnesql02/
path "secret/data/k8s/*" {
  capabilities = ["read"]
}
path "secret/metadata/k8s/*" {
  capabilities = ["list", "read"]
}
path "secret/data/db/*" {
  capabilities = ["read"]
}
path "secret/metadata/db/*" {
  capabilities = ["list", "read"]
}
EOF

# create 'ansible' approle policy
vault write auth/approle/role/ansible     secret_id_ttl=0     token_num_uses=0     token_ttl=20m     token_max_ttl=30m     token_policies=ansible-read-policy

# role id of approle
export ROLE_ID="$(vault read -field=role_id auth/approle/role/ansible/role-id)"
echo $ROLE_ID

# generate a secret (used to create tokens later)
export SECRET_ID="$(vault write -force -field=secret_id auth/approle/role/ansible/secret-id)"
echo $SECRET_ID
```

You'll need to put those role IDs and secret IDS into [`ansible/vault-login.sh.sample`](../ansible/vault-login.sh.sample) and copy it to `ansible/vault-login.sh`

Notice the policy grants access to the 'db' path in vault, which will hold database credentials; see [SETUP-DATABASE.md](SETUP-DATABASE.md) on creating those.
