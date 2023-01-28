#!/usr/bin/bash
#
# Usage: upload-certificate.sh HOSTNAME PATH
#
# Uploads the base64 form of a site certificate to vault. You must already be logged into vault. 

HOST=$1
PATH=$2

echo Uploading to vault path ................. ${PATH}
echo Uploading private key ................... private/${HOST}-key.pem
cat private/${HOST}-key.pem | base64 -w0 | vault kv patch -mount=secret "${PATH}" tls.key=-

echo Uploading site certificate .............. cert/${HOST}.pem
cat cert/${HOST}.pem        | base64 -w0 | vault kv put   -mount=secret "${PATH}" tls.crt=-

echo Setting vault metadata for path ......... ${PATH}
vault kv metadata put -mount=secret -custom-metadata=description="TLS certificate for ${HOST}" "${PATH}"

echo Done
