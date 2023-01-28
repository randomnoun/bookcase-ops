#!/usr/bin/bash
#
# Usage: create-certificate.sh HOSTNAME
#
# Creates a private key and site certificate for the given HOSTNAME

HOST=$1
SUBJECT="/C=AU/ST=QLD/L=Brisbane/O=randomnoun/CN=${HOST}"

if []; then
  echo "Usage: create-certificate.sh HOSTNAME"
  exit 1
fi

echo Generating private key .................. private/${HOST}-key.pem
openssl genrsa -out private/${HOST}-key.pem  4096

echo Generating certificate signing request .. csr/${HOST}.csr
openssl req -new -sha256 -key csr/${HOST}-key.pem -out private/${HOST}.csr -subj "${SUBJECT}"

echo Generating certificate extensions ....... ext/${HOST}.ext
echo "authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment
subjectAltName = @alt_names
[alt_names]
DNS.1 = ${HOST}" > ext/${HOST}.ext

echo Generating site certificate ............. cert/${HOST}.pem
openssl x509 -req -in private/${HOST}.csr -CA ca/randomnoun-cacert.pem -CAkey private/randomnoun-cacert-key.pem -CAcreateserial -out cert/${HOST}.pem -days 36525 -sha256 -extfile ext/${HOST}.ext