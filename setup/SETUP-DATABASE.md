# Setting up the database

Most of the applications in bookcase-ops use their own persistence mechanisms to store data on the volumes attached by kubernetes, but xwiki uses an external database, so that needs to be created and configured.

I'm hosting the database on `bnesql02`, in the schema `xwiki`,connecting with the username `xwiki`.

## Creating the `xwiki` database and `xwiki` user

Steps to do that ( [via](https://www.xwiki.org/xwiki/bin/view/Documentation/AdminGuide/Installation/InstallationWAR/InstallationMySQL/) ):

Replace `super-secret-password` with some random mumbojumbo.

```
mysql -u root -e "CREATE DATABASE xwiki DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;"
mysql -u root -e "CREATE USER 'xwiki'@'%' IDENTIFIED BY 'super-secret-password'";
mysql -u root -e "GRANT ALL PRIVILEGES ON *.* TO xwiki@'%';"
```

The root password for mysql is the one you set in `vars.json` ( from [vars.json.sample](../packer-ubuntu-mysql/src/main/packer/vars.json.sample) )

## Adding credentials to vault 

Replace `super-secret-password` with the random mumbojumbo you used above.

```
vault login
echo -n super-secret-password  | vault kv put   -mount=secret "db/bnesql02/xwiki" password=-
vault kv metadata put -mount=secret -custom-metadata=description="database credentials" "db"
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for bnesql02.dev.randomnoun" "db/bnesql02"
vault kv metadata put -mount=secret -custom-metadata=description="database credentials for xwiki user on bnesql02.dev.randomnoun" "db/bnesql02/xwiki"
```

