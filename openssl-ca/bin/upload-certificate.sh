#!/usr/bin/bash
#
# Usage: upload-certificate.sh HOSTNAME SECRET_PATH
#
# Where 
#   HOSTNAME is the hostname of the certificate
#   SECRET_PATH is the path of where to store the secret in vault 
#
# Uploads the base64 form of a site certificate to vault. You must already be logged into vault. 

HOST=$1
SECRET_PATH=$2

if [[ $# -ne 2 ]] ; then
    echo "Usage: upload-certificate.sh HOSTNAME SECRET_PATH"
    exit 1
fi

echo Uploading to vault path ................. ${SECRET_PATH}
echo Uploading private key ................... private/${HOST}-key.pem
cat private/${HOST}-key.pem | base64 -w0 | vault kv put   -mount=secret "${SECRET_PATH}" tls.key=-

echo Uploading site certificate .............. cert/${HOST}.pem
cat cert/${HOST}.pem        | base64 -w0 | vault kv patch -mount=secret "${SECRET_PATH}" tls.crt=-

echo Setting vault metadata for path ......... ${PATH}
vault kv metadata put -mount=secret -custom-metadata=description="TLS certificate for ${HOST}" "${SECRET_PATH}"

echo Done
