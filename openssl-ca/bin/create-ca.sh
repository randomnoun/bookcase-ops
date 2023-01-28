echo "Creating CA certificate"

# create a key for the certificate authority
openssl genrsa -out private/cacert-key.pem  4096

# the next command will prompt for country, state, email address
openssl req -new -sha256 -key private/cacert-key.pem -x509 -nodes -days 36525 -out ca/cacert.pem

sudo cp ca/cacert.pem /usr/local/share/ca-certificates/cacert.crt
sudo update-ca-certificates

