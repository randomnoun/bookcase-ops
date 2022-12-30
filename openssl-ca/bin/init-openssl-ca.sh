echo "Creating directories under /opt/openssl-ca"

sudo mkdir -p /opt/openssl-ca
sudo chown $USER:$USER /opt/openssl-ca
cd /opt/openssl-ca
mkdir bin
mkdir ca
mkdir private
mkdir csr
mkdir ext
mkdir cert 