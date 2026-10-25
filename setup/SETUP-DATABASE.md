# Setting up the database

Most of the applications in bookcase-ops use their own persistence mechanisms to store data on the volumes attached by kubernetes, 
but some containers use an external database, so those needs to be created and configured.

I'm hosting the databases on `bnesql02`.


   * mysql: xwiki, commafeed, wakapi
      * When you're creating these, you'll need to use the `root` mysql user. The `root` password for mysql is the one you set in `vars.json` ( from [vars.json.sample](../packer-ubuntu-mysql/src/main/packer/vars.json.sample) )
   * postgres: atuin, litellm, langfuse, openwebui, openhands
      * When creating these, use the `postgres` user ( password also in `vars.json` ).
   * clickhouse: langfuse    

Each application has a schema and username the same name as the application (e.g. for xwiki, the schema is `xwiki`, and the application connects with the username `xwiki` ).


## Creating the `xwiki` database and `xwiki` user

Steps to do that ( [via](https://www.xwiki.org/xwiki/bin/view/Documentation/AdminGuide/Installation/InstallationWAR/InstallationMySQL/) ):

Replace `super-secret-password` with some random mumbojumbo.

```
mysql -u root -e "CREATE DATABASE xwiki DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;" -p
mysql -u root -e "CREATE USER 'xwiki'@'%' IDENTIFIED BY 'super-secret-password';" -p
mysql -u root -e "GRANT ALL PRIVILEGES ON xwiki.* TO xwiki@'%';" -p
```

## Creating the `commafeed` database and `commafeed` user

Steps to do that:

Replace `super-secret-password` with some random mumbojumbo, different to the random mumbojumbo you used for the xwiki user.

```
mysql -u root -e "CREATE DATABASE commafeed DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;" -p
mysql -u root -e "CREATE USER 'commafeed'@'%' IDENTIFIED BY 'super-secret-password';" -p
mysql -u root -e "GRANT ALL PRIVILEGES ON commafeed.* TO commafeed@'%';" -p
```

## Creating the `wakapi` database and `wakapi` user

Steps to do that:

Replace `super-secret-password` with some random mumbojumbo, different to the random mumbojumbo you used for the xwiki user.

```
mysql -u root -e "CREATE DATABASE wakapi DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;" -p
mysql -u root -e "CREATE USER 'wakapi'@'%' IDENTIFIED BY 'super-secret-password';" -p
mysql -u root -e "GRANT ALL PRIVILEGES ON wakapi.* TO wakapi@'%';" -p
```

You'll also need to configure some salt for passwords stored in this schema, see below

## Creating the `atuin` database and `atuin` user

Steps to do that:

Replace `super-secret-password` with some random mumbojumbo, different to the random mumbojumbo you used for the other users.

```
psql -h localhost -U postgres -c "CREATE USER atuin WITH PASSWORD 'super-secret-password';"
psql -h localhost -U postgres -c "CREATE DATABASE atuin OWNER atuin;"
```

## Creating the `litellm` database and `litellm` user

Steps to do that:

Replace `super-secret-password` with some random mumbojumbo, different to the random mumbojumbo you used for the other users.

```
psql -h localhost -U postgres -c "CREATE USER litellm WITH PASSWORD 'super-secret-password';"
psql -h localhost -U postgres -c "CREATE DATABASE litellm OWNER litellm;"
```

## Creating the `langfuse` database and `langfuse` user

This holds langfuse's transactional data ( users, projects, API keys ). The traces themselves live in clickhouse, and the raw events in rustfs.

Steps to do that:

Replace `super-secret-password` with some random mumbojumbo, different to the random mumbojumbo you used for the other users.

```
psql -h localhost -U postgres -c "CREATE USER langfuse WITH PASSWORD 'super-secret-password';"
psql -h localhost -U postgres -c "CREATE DATABASE langfuse OWNER langfuse;"
```

## Creating the `langfuse` clickhouse database and user

Steps to do that:

`default-password` is the password used when creating the clickhouse database ( in vars.json )

```
export CLICKHOUSE_PASSWORD=default-password
clickhouse-client --query "CREATE DATABASE langfuse"
clickhouse-client --query "CREATE USER langfuse IDENTIFIED WITH sha256_password BY 'super-secret-password'"
clickhouse-client --query "GRANT ALL ON langfuse.* TO langfuse"
```

Langfuse runs against a single node, so there's no clickhouse cluster to configure ( `CLICKHOUSE_CLUSTER_ENABLED` is false ).

## Creating the `langfuse` rustfs bucket and access key

Langfuse also stores incoming events and media in S3-compatible storage. I'm using [rustfs](https://rustfs.com) on `bnenas05`, 
with the S3 API on port `30292` and the web UI on port `30293`.

Langfuse gets its own user, restricted to its own bucket, rather than the rustfs admin credentials. 
You can do this in the rustfs web UI ( create a bucket called `langfuse`, a user called `langfuse`, and a policy allowing `s3:*` on that bucket, attached to that user ), 
or with the [`rc`](https://github.com/rustfs/cli) command line client, with an alias set up using the admin credentials:

```
rc alias set bnenas05 http://bnenas05.dev.randomnoun:30292 rustfs-admin-access-key rustfs-admin-secret-key

rc mb bnenas05/langfuse

cat > langfuse-policy.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["s3:*"], "Resource": ["arn:aws:s3:::langfuse", "arn:aws:s3:::langfuse/*"] }
  ]
}
EOF

rc admin user add bnenas05/ langfuse super-secret-secret-key
rc admin policy create bnenas05/ langfuse-readwrite langfuse-policy.json
rc admin policy attach bnenas05/ langfuse-readwrite --user langfuse
```

The access key id is `langfuse` and the secret access key is whatever you used above. 

To check the new user can write to the bucket, before involving langfuse:

```
rc alias set langfuse-test http://bnenas05.dev.randomnoun:30292 langfuse super-secret-secret-key
echo hello | rc pipe langfuse-test/langfuse/test.txt
rc ls langfuse-test/langfuse/
rc rm langfuse-test/langfuse/test.txt
```

## Creating the `openwebui` database and `openwebui` user

Steps to do that:

Replace `super-secret-password` with some random mumbojumbo, different to the random mumbojumbo you used for the other users.

```
psql -h localhost -U postgres -c "CREATE USER openwebui WITH PASSWORD 'super-secret-password';"
psql -h localhost -U postgres -c "CREATE DATABASE openwebui OWNER openwebui;"
```

## Creating the `openhands` database and `openhands` user

This is only used by the OpenHands automation backend ( conversations and settings are files on its PVC ).

Replace `super-secret-password` with some random mumbojumbo, different to the random mumbojumbo you used for the other users.

```
psql -h localhost -U postgres -c "CREATE USER openhands WITH PASSWORD 'super-secret-password';"
psql -h localhost -U postgres -c "CREATE DATABASE openhands OWNER openhands;"
```

## Adding database credentials to vault 

Replace `super-secret-password` with the random mumbojumbo you used for each database/user above.

```
vault login
echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/xwiki" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for xwiki user on bnesql02.dev.randomnoun" "db/bnesql02/xwiki"

echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/commafeed" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for commafeed user on bnesql02.dev.randomnoun" "db/bnesql02/commafeed"

echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/wakapi" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for wakapi user on bnesql02.dev.randomnoun" "db/bnesql02/wakapi"

echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/atuin" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for atuin user on bnesql02.dev.randomnoun" "db/bnesql02/atuin"

echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/litellm" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for litellm user on bnesql02.dev.randomnoun" "db/bnesql02/litellm"

echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/langfuse" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for langfuse user on bnesql02.dev.randomnoun" "db/bnesql02/langfuse"

echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/openwebui" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for openwebui user on bnesql02.dev.randomnoun" "db/bnesql02/openwebui"

echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/openhands" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for openhands user on bnesql02.dev.randomnoun" "db/bnesql02/openhands"

```

## Adding application credentials to vault

**wakapi** requires some salt for passwords

```
# note 'patch' verb, not 'put'
echo -n super-secret-password  | vault kv patch  -mount=secret "db/bnesql02/wakapi" salt=-
```

**litellm** needs a master key, and a salt key ( these need to start with "sk-" )

```
echo -n "sk-$(openssl rand -hex 24)" | vault kv patch -mount=secret "db/bnesql02/litellm" master_key=-

echo -n "sk-$(openssl rand -hex 24)" | vault kv patch -mount=secret "db/bnesql02/litellm" salt_key=-

# for first login
vault kv get -mount=secret -field=master_key "db/bnesql02/litellm"
``` 

**langfuse** needs a handful of secrets. 

* `clickhouse_password` and `s3_secret_access_key` are the ones you used when creating the langfuse user in clickhouse, and the langfuse access key in rustfs above.
* redis runs in the cluster, so that password can be anything. 
* The `public_key` and `secret_key` are the API keys for the initial project; langfuse creates the project with those keys on first startup,  and litellm reads the same two values to send its traces ( so langfuse needs creating before litellm is re-deployed ). 
* The `init_user_password` is what you log in to the UI with, as the `langfuse_init_user_email` in the vars file.

```
echo -n "super-secret-password"        | vault kv put -mount=secret "k8s/bnekub03/secret/dev-langfuse/langfuse" clickhouse_password=-
echo -n "super-secret-secret-key"      | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-langfuse/langfuse" s3_secret_access_key=-
echo -n "$(openssl rand -hex 16)"      | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-langfuse/langfuse" init_user_password=-

echo -n "$(openssl rand -hex 24)"      | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-langfuse/langfuse" nextauth_secret=-
echo -n "$(openssl rand -hex 24)"      | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-langfuse/langfuse" salt=-
echo -n "$(openssl rand -hex 32)"      | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-langfuse/langfuse" encryption_key=-
echo -n "$(openssl rand -hex 24)"      | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-langfuse/langfuse" redis_password=-
echo -n "pk-lf-$(openssl rand -hex 16)" | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-langfuse/langfuse" public_key=-
echo -n "sk-lf-$(openssl rand -hex 16)" | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-langfuse/langfuse" secret_key=-

# for first login
vault kv get -mount=secret -field=init_user_password "k8s/bnekub03/secret/dev-langfuse/langfuse"
```

**sonar** can use a HuggingFace access token ( a read-only one is enough ) for faster, rate-limit-free model downloads. Create one at huggingface.co/settings/tokens:

```
echo -n "hf_put-the-token-here" | vault kv put -mount=secret "k8s/bnekub03/secret/dev-sonar/sonar" hf_token=-
```

**searxng** needs a secret key for CSRF/session signing:

```
echo -n "$(openssl rand -hex 24)" | vault kv put -mount=secret "k8s/bnekub03/secret/dev-searxng/searxng-secret-key" key=-
```

**tabbyapi** requires an api_key ( for litellm ), and an admin_key ( for loading and downloading)

```
echo -n "$(openssl rand -hex 24)" | vault kv put -mount=secret "k8s/bnekub03/secret/dev-tabbyapi/tabbyapi" api_key=-

echo -n "$(openssl rand -hex 24)" | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-tabbyapi/tabbyapi" admin_key=-
```

**open-webui** needs a secret key to sign login JWTs, and a litellm virtual key.

Create the key in the litellm UI via

* Internal Users -> Invite User
   * User email: open-webui-service
   * Global Proxy Role: Internal User ( Create/Delete/View )
   * Team: randomnoun
* Virtual Keys -> Create new key
   * Owned by: Another user 
   * User ID: open-webui-service
   * Team: randomnoun
   * Key name: open-webui
   * Key type: AI APIs

```
echo -n "$(openssl rand -base64 32)" | vault kv put   -mount=secret "k8s/bnekub03/secret/dev-open-webui/open-webui" webui_secret_key=-
echo -n "put-the-litellm-virtual-key-here"  | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-open-webui/open-webui" litellm_virtual_key=-
```

and for **pi**, **opencode** and **openhands**, a litellm virtual key each ( create them the same way as the open-webui one, 
using `pi-service` / `opencode-service` / `openhands-service` as the user and `pi` / `opencode` / `openhands` as the key name ).

pi and opencode read theirs from vault, and both list whatever models that key can see in litellm. 

opencode also gets a password protecting its HTTP API:

```
echo -n "put-the-pi-litellm-virtual-key-here"       | vault kv put   -mount=secret "k8s/bnekub03/secret/dev-pi/pi" litellm_virtual_key=-

echo -n "put-the-opencode-litellm-virtual-key-here" | vault kv put   -mount=secret "k8s/bnekub03/secret/dev-opencode/opencode" litellm_virtual_key=-
echo -n "$(openssl rand -hex 24)"                   | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-opencode/opencode" server_password=-
```

**openhands** doesn't read its litellm key from vault; you paste it into the OpenHands settings UI instead ( see [SETUP-LLM-FRONTEND.md](SETUP-LLM-FRONTEND.md#openhands) ). 
Vault only needs the key you'll be asked for when you first open the UI:

```
echo -n "$(openssl rand -hex 24)" | vault kv put -mount=secret "k8s/bnekub03/secret/dev-openhands/openhands" session_api_key=-

# for first login
vault kv get -mount=secret -field=session_api_key "k8s/bnekub03/secret/dev-openhands/openhands"
```




