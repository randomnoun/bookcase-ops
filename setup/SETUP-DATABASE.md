# Setting up the database

Most of the applications in bookcase-ops use their own persistence mechanisms to store data on the volumes attached by kubernetes, 
but xwiki, commafeed and atuin use an external database, so those needs to be created and configured.

I'm hosting the databases on `bnesql02`.

For xwiki, the schema is `xwiki`, and the application connects with the username `xwiki`.

For commafeed, the schema is `commafeed`, and the application connects with the username `commafeed`.

When you're creating these, you'll need to use the `root` mysql user. The `root` password for mysql is the one you set in `vars.json` ( from [vars.json.sample](../packer-ubuntu-mysql/src/main/packer/vars.json.sample) )

For atuin, the schema is `atuin`, and the application connects with the username `atuin`. ( Note atuin uses postgres, not mysql )

When creating this, use the `postgres` user ( password also in `vars.json` ).

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

echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/openwebui" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for openwebui user on bnesql02.dev.randomnoun" "db/bnesql02/openwebui"

echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/openhands" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for openhands user on bnesql02.dev.randomnoun" "db/bnesql02/openhands"

```

## Adding application credentials to vault

And some salt for **wakapi** passwords

```
# note 'patch' verb, not 'put'
echo -n super-secret-password  | vault kv patch  -mount=secret "db/bnesql02/wakapi" salt=-
```

and a master key + salt key for **litellm** ( these need to start with "sk-" )

```
echo -n "sk-$(openssl rand -hex 24)" | vault kv patch -mount=secret "db/bnesql02/litellm" master_key=-

echo -n "sk-$(openssl rand -hex 24)" | vault kv patch -mount=secret "db/bnesql02/litellm" salt_key=-

# for first login
vault kv get -mount=secret -field=master_key "db/bnesql02/litellm"
``` 

and a secret key for **searxng** for it's CSRF/session signing:

```
echo -n "$(openssl rand -hex 24)" | vault kv put -mount=secret "k8s/bnekub03/secret/dev-searxng/searxng-secret-key" key=-
```

and for **open-webui**, a secret key to sign login JWTs, and the litellm virtual key.

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
opencode also gets a password protecting its HTTP API ( it can run shell commands, so don't leave it open ):

```
echo -n "put-the-pi-litellm-virtual-key-here"       | vault kv put   -mount=secret "k8s/bnekub03/secret/dev-pi/pi" litellm_virtual_key=-

echo -n "put-the-opencode-litellm-virtual-key-here" | vault kv put   -mount=secret "k8s/bnekub03/secret/dev-opencode/opencode" litellm_virtual_key=-
echo -n "$(openssl rand -hex 24)"                   | vault kv patch -mount=secret "k8s/bnekub03/secret/dev-opencode/opencode" server_password=-
```

**openhands** doesn't read its litellm key from vault; you paste it into the OpenHands settings UI instead ( see [SETUP-LLM.md](SETUP-LLM.md) ). 
Vault only needs the key you'll be asked for when you first open the UI:

```
echo -n "$(openssl rand -hex 24)" | vault kv put -mount=secret "k8s/bnekub03/secret/dev-openhands/openhands" session_api_key=-
# for first login
vault kv get -mount=secret -field=session_api_key "k8s/bnekub03/secret/dev-openhands/openhands"
```




