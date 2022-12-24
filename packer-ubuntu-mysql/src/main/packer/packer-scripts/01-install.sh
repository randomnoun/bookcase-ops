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
BACKUP_HOST=${BACKUP_HOST}
BACKUP_PATH=${BACKUP_PATH}
BACKUP_USERNAME=${BACKUP_USERNAME}
BACKUP_PASSWORD=${BACKUP_PASSWORD}
EOF

echo '>>>> Backing up'
# an empty directory

mkdir -p /opt/backup

sshpass -p ${BACKUP_PASSWORD}  scp -o 'StrictHostKeyChecking no' -r /opt/backup ${BACKUP_USERNAME}@${BACKUP_HOST}:${BACKUP_PATH}

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

echo '>>>> ip addr show'
ip addr show

exit 0
