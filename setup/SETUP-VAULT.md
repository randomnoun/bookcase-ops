# SETUP-VAULT.md

Vault acts as a repository for credentials (usernames / passwords / certificates).  

You might want to install vault on the same VM that you've installed your database server on, 
because they're both things you wouldn't want kubernetes having any control over whatsoever, for obvious reasons.

I'm installing the vault manually rather than scripting it.

## Installing the vault:

```
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install vault
vault
vault server -help
vault server -dev
```

Once it's installed, you might want to set up a name in your DNS that points to the server. 

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

## Unsealing the vault:

```
vault operator unseal asdfasdfasdfasdfSGUf635zZnHTh+FdGCkbt0lxmGiE
vault operator unseal asdfasdfasdfasdfE5v2MwAGnCkFAOGczwgagJ6rGYl0
vault operator unseal asdfasdfasdfasdfY8yNWpKfYAVtRbJhjUnhy2D0by6T
```

## Creating the 'secret' key-value ( kv ) store

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

This is all you'll need to get the packer scripts running. 

Once you're running ansible, you'll need to create more secrets for it; see [SETUP-CERTIFICATE.md](SETUP-CERTIFICATE.md)

 
