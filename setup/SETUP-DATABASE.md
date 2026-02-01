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
mysql -u root -e "GRANT ALL PRIVILEGES ON *.* TO xwiki@'%';" -p
```

## Creating the `commafeed` database and `commafeed` user

Steps to do that:

Replace `super-secret-password` with some random mumbojumbo, different to the random mumbojumbo you used for the xwiki user.

```
mysql -u root -e "CREATE DATABASE commafeed DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;" -p
mysql -u root -e "CREATE USER 'commafeed'@'%' IDENTIFIED BY 'super-secret-password';" -p
mysql -u root -e "GRANT ALL PRIVILEGES ON *.* TO commafeed@'%';" -p
```

## Creating the `atuin` database and `atuin` user

Steps to do that:

Replace `super-secret-password` with some random mumbojumbo, different to the random mumbojumbo you used for the other users.

```
psql -h localhost -U postgres -c "CREATE USER atuin WITH PASSWORD 'super-secret-password';"
psql -h localhost -U postgres -c "CREATE DATABASE atuin OWNER atuin;"
```

## Adding credentials to vault 

Replace `super-secret-password` with the random mumbojumbo you used for each database/user above.

```
vault login
echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/xwiki" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for xwiki user on bnesql02.dev.randomnoun" "db/bnesql02/xwiki"

echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/commafeed" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for commafeed user on bnesql02.dev.randomnoun" "db/bnesql02/commafeed"

echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/atuin" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for atuin user on bnesql02.dev.randomnoun" "db/bnesql02/atuin"
```

