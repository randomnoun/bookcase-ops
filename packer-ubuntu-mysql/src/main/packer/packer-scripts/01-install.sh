#!/bin/bash

set -o xtrace

# All output to LOGFILE and to terminal
LOGFILE=/opt/packer/packer-install-log.txt
mkdir -p /opt/packer
exec > >(tee ${LOGFILE}) 2>&1

echo '>>>> 01-install.sh'
echo "All output logged to ${LOGFILE}"

echo '>>>> Environment'
set

echo '>>>> ip addr show'
ip addr show

if [ `id -u` -ne 0 ] ; then echo "Please run as root using sudo" ; exit 1 ; fi

echo '>>>> Installing base apt packages'

apt-get update
apt-get install -y open-vm-tools
apt-get install -y bc jq sshpass curl net-utils

echo '>>>> Creating /root/backup-variables.sh'

cat << EOF > /root/backup-variables.sh
#! /bin/bash
NAS_HOST=${NAS_HOST}
NAS_BACKUP_PATH=${NAS_BACKUP_PATH}
NAS_USERNAME=${NAS_USERNAME}
NAS_PASSWORD=${NAS_PASSWORD}
EOF

echo '>>>> Backing up'
# an empty directory

mkdir -p /opt/backup

sshpass -p ${NAS_PASSWORD}  scp -o 'StrictHostKeyChecking no' -r /opt/backup ${NAS_USERNAME}@${NAS_HOST}:${NAS_BACKUP_PATH}

echo '>>>> Copying files from /tmp/packer to filesystem'
 
mv /tmp/packer/root/.inputrc ${HOME}/.inputrc

echo '>>>> Installing mysql'
echo mysql-server mysql-server/root_password select ${MYSQL_ROOT_PASSWORD} | debconf-set-selections
echo mysql-server mysql-server/root_password_again select ${MYSQL_ROOT_PASSWORD} | debconf-set-selections
apt-get install -y mysql-server

echo '>>>> Creating root user'
echo "ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '${MYSQL_ROOT_PASSWORD}';" | mysql -uroot -p${MYSQL_ROOT_PASSWORD}
echo "CREATE USER 'root'@'%' IDENTIFIED WITH mysql_native_password BY '${MYSQL_ROOT_PASSWORD}';" | mysql -uroot -p${MYSQL_ROOT_PASSWORD}
echo "GRANT ALL PRIVILEGES ON *.* TO 'root'@'%';" | mysql -uroot -p${MYSQL_ROOT_PASSWORD}

echo '>>>> Binding mysql to 0.0.0.0 (all interfaces)'
mv /tmp/packer/etc/mysql/mysql.conf.d/mysqld_bind_all_interfaces.cnf /etc/mysql/mysql.conf.d/mysqld_bind_all_interfaces.cnf
service mysql restart

# for atuin
echo '>>>> Installing postgres'
apt install postgresql postgresql-contrib

echo '>>>> Configuring postgres user'
sudo -u postgres psql -c "ALTER USER postgres PASSWORD '${POSTGRES_PASSWORD}';"

echo '>>>> Configuring postgres'
PG_VER=$(psql --version | grep -oE '[0-9]+' | head -n1)
CONF_DIR="/etc/postgresql/$PG_VER/main"

echo ">>>> Detected PostgreSQL version: $PG_VER"
# changes '#listen_addresses = 'localhost'' to 'listen_addresses = '*''
sed -i "s/^#\?listen_addresses = .*/listen_addresses = '*'/" "$CONF_DIR/postgresql.conf"
# add remote access rule to pg_hba.conf
if ! grep -q "0.0.0.0/0" "$CONF_DIR/pg_hba.conf"; then
    echo "host    all             all             0.0.0.0/0               scram-sha-256" | tee -a "$CONF_DIR/pg_hba.conf"
fi
# sudo ufw allow 5432/tcp
systemctl restart postgresql

# for langfuse
echo '>>>> Installing clickhouse'
apt-get install -y apt-transport-https ca-certificates curl gnupg jq
curl -fsSL 'https://packages.clickhouse.com/rpm/lts/repodata/repomd.xml.key' | gpg --dearmor -o /usr/share/keyrings/clickhouse-keyring.gpg
ARCH=$(dpkg --print-architecture)
echo "deb [signed-by=/usr/share/keyrings/clickhouse-keyring.gpg arch=${ARCH}] https://packages.clickhouse.com/deb stable main" | tee /etc/apt/sources.list.d/clickhouse.list
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y clickhouse-server clickhouse-client

echo '>>>> Configuring clickhouse default user password'
# stored as a sha256 hash, which also avoids having to xml-escape the password
CLICKHOUSE_PASSWORD_SHA256=$(echo -n "${CLICKHOUSE_PASSWORD}" | sha256sum | cut -d' ' -f1)
tee /etc/clickhouse-server/users.d/default-password.xml <<EOF
<clickhouse>
    <users>
        <default>
            <password remove="1"/>
            <password_sha256_hex>${CLICKHOUSE_PASSWORD_SHA256}</password_sha256_hex>
        </default>
    </users>
</clickhouse>
EOF
chown clickhouse:clickhouse /etc/clickhouse-server/users.d/default-password.xml
chmod 600 /etc/clickhouse-server/users.d/default-password.xml

echo '>>>> Binding clickhouse to 0.0.0.0 (all interfaces)'
tee /etc/clickhouse-server/config.d/listen.xml <<EOF
<clickhouse>
    <listen_host>0.0.0.0</listen_host>
</clickhouse>
EOF
chown clickhouse:clickhouse /etc/clickhouse-server/config.d/listen.xml

systemctl enable clickhouse-server
systemctl start clickhouse-server

echo '>>>> ip addr show'
ip addr show

exit 0
