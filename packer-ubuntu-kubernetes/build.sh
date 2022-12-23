#!/usr/bin/env bash
set -e

# uncomment to debug
# set -o xtrace

PACKER_VARS=vars.json
PACKER_HCL=ubuntu-kubernetes.pkr.hcl
WITH_VAULT=1

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

if [[ "${WITH_VAULT}" -eq "1" ]]; then
    function CHECK_TOKEN() {
        BAD_TOKEN=0
        if [[ -z "${VAULT_TOKEN}" ]]; then
            echo VAULT_TOKEN not defined
            BAD_TOKEN=1
        elif ! vault token lookup; then
            echo VAULT_TOKEN not valid
            BAD_TOKEN=1
        fi
    }

    CHECK_TOKEN

    if [[ "${BAD_TOKEN}" -eq "1" ]]; then
        if [ -x ./vault-login.sh ]; then
            echo "Running vault-login.sh"
            source vault-login.sh
            CHECK_TOKEN
        fi
        if [[ "${BAD_TOKEN}" -eq "1" ]]; then
            echo "VAULT_TOKEN not defined; you may need to obtain a token via:"
            echo "export VAULT_TOKEN=$(vault write -format=json auth/approle/login role_id="..." secret_id="..." | jq -r .auth.client_token)"
            exit 1
        fi
    fi
    
    CLOUD_INIT_USERNAME=$(vault kv get -mount=secret -field=username packer/cloud-init)
    CLOUD_INIT_FULLNAME=$(vault kv get -mount=secret -field=fullname packer/cloud-init)
    CLOUD_INIT_PASSWORD=$(vault kv get -mount=secret -field=password packer/cloud-init)
    CLOUD_INIT_PASSWORD_HASH=$(vault kv get -mount=secret -field=password-hash packer/cloud-init)
    CLOUD_INIT_AUTHORIZED_KEYS=$(vault kv get -mount=secret -field=authorized-keys packer/cloud-init)
    
else

    CLOUD_INIT_USERNAME=$(cat ${SRC_PACKER}/${PACKER_VARS} | jq -j .cloud_init_username)
    CLOUD_INIT_FULLNAME=$(cat ${SRC_PACKER}/${PACKER_VARS} | jq -j .cloud_init_fullname)
    CLOUD_INIT_PASSWORD=$(cat ${SRC_PACKER}/${PACKER_VARS} | jq -j .cloud_init_password)
    CLOUD_INIT_PASSWORD_HASH=$(cat ${SRC_PACKER}/${PACKER_VARS} | jq -j .cloud_init_password_hash)
    CLOUD_INIT_AUTHORIZED_KEYS=$(cat ${SRC_PACKER}/${PACKER_VARS} | jq -j .cloud_init_authorized_keys)

fi

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

echo VAULT_TOKEN is ${VAULT_TOKEN}

# uncomment to view all variables 
# packer inspect -var-file=${PACKER_VARS} ${PACKER_HCL}

packer build -var-file=${PACKER_VARS} -on-error=ask ${PACKER_HCL} 


exit
