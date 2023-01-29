# SETUP-VAULT.md

Vault acts as a repository for credentials (usernames / passwords / certificates).  

You might want to install vault on the same VM that you've installed your database server on, 
because they're both things you wouldn't want kubernetes having any control over whatsoever, for obvious reasons.

I'm installing the vault manually rather than scripting it.

## Installing vault:

In a fairly typical depiction of what writing scriptable installations is going to be like for the rest of your life, the steps to do this changed a week after I wrote them down, so you might want to check what they are at [https://www.hashicorp.com/official-packaging-guide](hashicorp) if/when this fails again.

```
sudo /bin/bash -c 'wget -O- https://apt.releases.hashicorp.com/gpg | gpg --dearmor > /usr/share/keyrings/hashicorp-archive-keyring.gpg'
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update
sudo apt install vault
```

## Configuring the vault:

I'm configuring the vault to listen to HTTP requests on port 8200 for now.

You could use HTTPS if you like, but the config is different, you'll need to create the certificates (which will require a CA which we don't have yet), 
and it'll be more difficult to debug.

Change the `vault.dev.randomnoun` domain name to what you want the domain to be.

```
cd /etc/vault.d
sudo cp vault.hcl vault.hcl.orig
sudo /bin/bash -c 'sudo cat << EOF > /etc/vault.d/vault.hcl
# Full configuration options can be found at https://www.vaultproject.io/docs/configuration

ui = true

storage "file" {
  path = "/opt/vault/data"
}

# HTTP listener
listener "tcp" {
  address = "0.0.0.0:8200"
  tls_disable = 1
  custom_response_headers {
    "default" = {
       "Strict-Transport-Security" = [""]
    }
  }

}
api_addr = "http://vault.dev.randomnoun:8200"
EOF'

sudo service vault start
sudo service vault status
```

Once it's installed and configured, you might want to set up a name in your DNS that points to the server. 

I'm using `vault.dev.randomnoun`, CNAMEd to the same host as my database server ( `bnesql02.dev.randomnoun` ). 

## Initialising the vault:

```
knoxg@bnesql02:/etc/vault.d$ export VAULT_ADDR='http://vault.dev.randomnoun:8200'
knoxg@bnesql02:/etc/vault.d$ vault operator init
Unseal Key 1: asdfasdfasdfasdfSGUf635zZnHTh+FdGCkbt0lxmGiE
Unseal Key 2: asdfasdfasdfasdfE5v2MwAGnCkFAOGczwgagJ6rGYl0
Unseal Key 3: asdfasdfasdfasdfY8yNWpKfYAVtRbJhjUnhy2D0by6T
Unseal Key 4: asdfasdfasdfasdfw+711GlIqIBKJoE6eR0hDDTl+uPb
Unseal Key 5: asdfasdfasdfasdfnuFXGjrJMK0Jd+eYkVWsdociKIDE

Initial Root Token: hvs.asdfasdfasdf89tOkTde8F30

Vault initialized with 5 key shares and a key threshold of 3. Please securely
distribute the key shares printed above. When the Vault is re-sealed,
restarted, or stopped, you must supply at least 3 of these keys to unseal it
before it can start servicing requests.

Vault does not store the generated root key. Without at least 3 keys to
reconstruct the root key, Vault will remain permanently sealed!

It is possible to generate new unseal keys, provided you have a quorum of
existing unseal keys shares. See "vault operator rekey" for more information.
```

Keep the output somewhere safe, because you'll need it to unseal and login to the vault. 

Tattoo it to the scalp of your first-born, or maybe just dump it into `/etc/vault.d/the-unseal-keys.txt`

## Unsealing the vault:

Any time the vault server restarts ( including the first time it starts ), you'll need to 'unseal' it.
Use any 3 of the keys in the output above to unseal it, via something like:

```
vault operator unseal asdfasdfasdfasdfSGUf635zZnHTh+FdGCkbt0lxmGiE
vault operator unseal asdfasdfasdfasdfE5v2MwAGnCkFAOGczwgagJ6rGYl0
vault operator unseal asdfasdfasdfasdfY8yNWpKfYAVtRbJhjUnhy2D0by6T
```

## Check the UI

Now the vault is up and running, try navigating to the vault server via a web browser to see if it's running
( `http://vault.dev.randomnoun:8200` ). 

Until you create any alternate credentials, you'll need to enter the Initial Root Token from the output above to login.

## Creating the 'secret' key-value ( kv ) store

Use the Initial Root Token to login via the command-line as well.

```
export VAULT_ADDR=http://vault.dev.randomnoun:8200

vault login
vault secrets enable -path=secret kv-v2
```

## Adding a bunch of secrets:

Modify this to contain your own username/passwords.

```
# Login credentials for ESXi server
echo -n knoxg        | vault kv put   -mount=secret packer/esxi/bnehyp05.dev.randomnoun username=-
echo -n pmfelxcnfI8u | vault kv patch -mount=secret packer/esxi/bnehyp05.dev.randomnoun password=-

# The initial user for virtual machines created by packer
echo -n knoxg        | vault kv put -mount=secret packer/cloud-init username=-
echo -n Greg Knox    | vault kv patch -mount=secret packer/cloud-init fullname=-
echo -n m7Z3Kx04Pmsm | vault kv patch -mount=secret packer/cloud-init password=-
PASSWORD_HASH=$(echo -n m7Z3Kx04Pmsm | mkpasswd --method=SHA-512 --rounds=4096)
echo -n ${PASSWORD_HASH} | vault kv patch -mount=secret packer/cloud-init password-hash=-

# Include an authorized_keys file to allow passwordless SSH access
echo -n '--the contents of your id_rsa.pub file--' | vault kv patch -mount=secret packer/cloud-init authorized-keys=-

# Credentials to access backup storage (over ssh)
# probably want to use a public cert here as well
echo -n knoxg        | vault kv put   -mount=secret packer/backup/bnenas04.dev.randomnoun username=-
echo -n Y5sdvJzHdGY3 | vault kv patch -mount=secret packer/backup/bnenas04.dev.randomnoun password=-

# Store some descriptions in vault as well
vault kv metadata put -mount=secret -custom-metadata=description='Login credentials for ESXi server' packer/esxi/bnehyp05.dev.randomnoun
vault kv metadata put -mount=secret -custom-metadata=description='The initial user for virtual machines created by packer' packer/cloud-init
vault kv metadata put -mount=secret -custom-metadata=description='Backup login credentials (over ssh)' packer/backup/bnenas04.dev.randomnoun
```

## Creating the packer role and generating a long-lived auth token:

```
vault policy write packer-read-policy - << EOF
# packer-read-policy
#
# gives read access to the secrets at
#   packer/esxi
#   packer/cloud-init
#   packer/backup
path "secret/data/packer/*" {
  capabilities = ["read"]
}
path "secret/metadata/packer/*" {
  capabilities = ["list", "read"]
}
EOF

vault auth enable approle

# create 'packer' approle policy
# you want secret_id_ttl=0 and token_num_uses=0 here so that secrets never expire, and that tokens can be reused any number of times within their time-to-live (ttl)
vault write auth/approle/role/packer     secret_id_ttl=0     token_num_uses=0     token_ttl=20m     token_max_ttl=30m     token_policies=packer-read-policy

# role id of approle
export ROLE_ID="$(vault read -field=role_id auth/approle/role/packer/role-id)"
echo $ROLE_ID

# generate a secret (used to create tokens later)
export SECRET_ID="$(vault write -f -field=secret_id auth/approle/role/packer/secret-id)"
echo $SECRET_ID
```

This should be all you need to get the packer scripts running. 

When you get round to running those, you'll need to enter that ROLE_ID and SECRET_ID into the [../packer-ubuntu-kubernetes/vault-login-sample.sh](vault-login.sh) files.

And once you're running ansible, you'll need to create more secrets for it; see [SETUP-CERTIFICATE.md](SETUP-CERTIFICATE.md)

