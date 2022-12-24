
# packer-ubuntu-mysql

**packer-ubuntu-mysql**  creates a MySQL server VM on ESX 6.0 using packer  

I'm running mysql on a VM rather than a k8s container because in my experience, k8s containers have half-lives that can be measured in days,
and I like my databases to not have the rug pulled out from underneath them every so often to really test their resilience to 
data corruption.

# Packer prerequisites

This version has been tested on packer 1.8.1 and has been updated to use the new, more complicated and arbitrarily different hcl format.

# Variables

Unlike the other virtual machines in this project, the mysql server does not use vault, so you'll have to put big bad passwords into the `vars.json` 
file.

Copy the [src/main/packer/vars.json.sample](src/main/packer/vars.json.sample) file to `src/main/packer/vars.json` and set your passwords there
before running the build script.

# Creating the VM 

```
./build.sh
```

I'm also using the mysql virtual machine to run the vault server, so hop on over to [../setup/SETUP-VAULT.md](SETUP-VAULT.md) on how to do that.


# Notes

* Ubuntu version: 22.04.1 ( live server amd64 install )
* MySQL version: the version bundled with ubuntu, which is 8.0.something
* Logs are written to /opt/packer/packer-install.log within the VM
* The mysql root password is updated as per the version file and downgraded to a `mysql_native_password` password to allow old clients to connect
* The mysql server is bound to all interfaces (0.0.0.0)

