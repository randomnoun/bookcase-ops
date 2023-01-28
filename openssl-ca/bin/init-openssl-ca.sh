echo "Creating directories under /opt/openssl-ca"

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

sudo mkdir -p /opt/openssl-ca
sudo chown $USER:$USER /opt/openssl-ca
cd /opt/openssl-ca
mkdir bin
mkdir ca
mkdir private
mkdir csr
mkdir ext
mkdir cert 

cp ${SCRIPT_DIR}/bin/* /opt/openssl-ca/bin
