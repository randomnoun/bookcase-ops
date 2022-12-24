#!/usr/bin/env bash
set -e

# uncomment to debug
# set -o xtrace

PACKER_VARS=vars.json
PACKER_HCL=ubuntu-mysql.pkr.hcl

# NB not using vault during installation as vault is going to be installed on this server

SRC_PACKER=src/main/packer
TARGET_PACKER=target/packer

# from https://stackoverflow.com/a/27776822
case "$(uname -sr)" in
   Darwin*)
     echo 'Mac OS X'
     export PACKER_CACHE_DIR=~/.packer_cache
     ;;
   Linux*Microsoft*)
     echo 'Running in Windows Subsystem for Linux'
     export PACKER_CACHE_DIR=~/.packer_cache
     ;;
   Linux*)
     echo 'Running on Linux'
     export PACKER_CACHE_DIR=~/.packer_cache
     ;;
   CYGWIN*)
     echo 'Running in cygwin'
     export PACKER_CACHE_DIR=`cygpath --windows ~/.packer_cache`
     ;;
   # Add here more strings to compare
   # See correspondence table at the bottom of this answer

   *)
     echo 'Unsupported OS'
     exit 
     ;;
esac

CLOUD_INIT_USERNAME=$(cat ${SRC_PACKER}/${PACKER_VARS} | jq -j .cloud_init_username)
CLOUD_INIT_FULLNAME=$(cat ${SRC_PACKER}/${PACKER_VARS} | jq -j .cloud_init_fullname)
CLOUD_INIT_PASSWORD=$(cat ${SRC_PACKER}/${PACKER_VARS} | jq -j .cloud_init_password)
CLOUD_INIT_PASSWORD_HASH=$(cat ${SRC_PACKER}/${PACKER_VARS} | jq -j .cloud_init_password_hash)
CLOUD_INIT_AUTHORIZED_KEYS=$(cat ${SRC_PACKER}/${PACKER_VARS} | jq -j .cloud_init_authorized_keys)

BUILDER_HOSTNAME=$(cat ${SRC_PACKER}/${PACKER_VARS} | jq -j .builder_hostname)

echo Creating ${BUILDER_HOSTNAME}
set

mkdir -p ${TARGET_PACKER}
rm -r ${TARGET_PACKER}
cp -r ${SRC_PACKER} ${TARGET_PACKER}

cat ${SRC_PACKER}/builder-http/user-data.template | \
  sed -e "s/\${CLOUD_INIT_USERNAME}/${CLOUD_INIT_USERNAME}/g" | \
  sed -e "s/\${CLOUD_INIT_FULLNAME}/${CLOUD_INIT_FULLNAME}/g" | \
  sed -e "s/\${CLOUD_INIT_PASSWORD}/${CLOUD_INIT_PASSWORD}/g" | \
  sed -e "s#\${CLOUD_INIT_PASSWORD_HASH}#${CLOUD_INIT_PASSWORD_HASH}#g" | \
  sed -e "s#\${CLOUD_INIT_AUTHORIZED_KEYS}#${CLOUD_INIT_AUTHORIZED_KEYS}#g" | \
  sed -e "s/\${BUILDER_HOSTNAME}/${BUILDER_HOSTNAME}/g" \
  > ${TARGET_PACKER}/builder-http/user-data
cd ${TARGET_PACKER}
export PACKER_LOG=1
export PACKER_LOG_PATH="../packerlog.txt"

# uncomment to initialise packer
# packer init .

# uncomment to view all variables 
# packer inspect -var-file=${PACKER_VARS} ${PACKER_HCL}

packer build -var-file=${PACKER_VARS} -on-error=ask ${PACKER_HCL} 


exit
