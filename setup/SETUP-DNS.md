
# Setting up DHCP / DNS

So you'll need a DNS server, because the DNS servers inside kubernetes seem to be mostly for directing traffic within kubernetes. And you'll need DHCP to bootstrap DNS.

I'm running these on a box outside the cluster, but I guess we could burn up some more resources on `bnehyp05` and do it there instead.

You could probably configure this on your wifi/internet router if your router has a DNS server in it, which it probably does.

In this setup, I'm using:

* **isc-dhcp-server** for DHCP ( IP address discovery, DNS discovery )
* **bind** to provide DNS ( domain name -> IP address ), and 

Actually, if you do install isc-dhcp-server / bind , you'll probably have to disable DHCP on your router, if your router has a DHCP server in it, which it probably does.

You might be able to get away with updating your hosts file ( `/etc/hosts` on linux, `C:\Windows\System32\Drivers\etc\hosts` on Windows ) instead of running bind.

## Installing isc-dhcp-server

```
sudo apt-get install isc-dhcp-server
```

## Configuring isc-dhcp-server

Add this to the end of /etc/dhcpd.conf:

```
# all the custom stuff
include "/etc/dhcp/dhcpd.conf.local";
```

Create a dhcpd.conf.local file containing:

```
# option definitions common to all supported networks
option domain-name "dev.randomnoun";

# DNS servers are bnehyp02, then TPG if it's down
# If TPG isn't your ISP then use your ISP's DNS here
option domain-name-servers 192.168.0.24, 203.12.160.35;

default-lease-time 600;
max-lease-time 7200;

# going to dish out IPs from 192.168.0.x
option subnet-mask 255.255.255.0;
option broadcast-address 192.168.0.255;
option routers 192.168.0.1;

# IPs for unknown devices will range from 192.168.0.200 - 192.168.0.250
subnet 192.168.0.0 netmask 255.255.255.0 {
  range 192.168.0.200 192.168.0.250;
}

# MAC -> IP mappings
include "/etc/dhcp/dhcpd-hosts.conf";
```

Create a `/etc/dhcp/dhcpd-hosts.conf` file containing:

```
# hardware devices that exist in the real world
host BNEHYP02   { hardware ethernet 18:03:73:C0:9A:6B; fixed-address 192.168.0.24 ; }
host BNEHYP05   { hardware ethernet 68:05:ca:9b:18:50; fixed-address 192.168.0.112 ; }
host BNENAS04   { hardware ethernet d0:50:99:c3:93:64; fixed-address 192.168.0.129 ; } # created 2022-08-20

# dev laptop
host EXCIMER        { hardware ethernet 80:FA:5B:9C:D8:9C; fixed-address 192.168.0.125 ; }
host EXCIMER-WIFI   { hardware ethernet F0:9E:4A:92:1F:99; fixed-address 192.168.0.126 ; }

# bnehyp05 VMs
# NB these MAC address were selected randomly and match the MACs in the packer scripts
host BNEKUB02   { hardware ethernet 00:00:20:2A:D3:CB; fixed-address 192.168.0.130 ; }
host BNENOD03   { hardware ethernet 00:00:B3:CC:14:DD; fixed-address 192.168.0.131 ; }
host BNESQL02   { hardware ethernet 00:0c:29:03:9d:d7; fixed-address 192.168.0.132 ; }
```

* Restart isc-dhcp-server via `sudo service isc-dhcp-server restart`
* Check status via `sudo service isc-dhcp-server status`
* Fix up any errors that might be appearing in `/var/log/syslog` 


## Installing bind

Steps to do that:

```
sudo apt-get install bind
```

## Configuring bind

OK so my local network will have a domain suffix of `.dev.randomnoun` and the hosts within that suffix are allocated IPs in the 192.168.0.x subnet.

The various hostnames and aliases below correspond to the various boxes on [SETUP-ARCHITECTURE.md](SETUP-ARCHITECTURE.md)

* Add this to the end of `/etc/bind/named.conf.local`

```
zone "dev.randomnoun" {
        type master;
        file "/etc/bind/db.dev.randomnoun";
};
zone "0.168.192.in-addr.arpa" {
        type master;
        notify no;
        file "/etc/bind/db.192";
};
```

* Create the file  `/etc/bind/db.dev.randomnoun` containing:

```
;
; BIND data file for dev.randomnoun zone
;
$TTL    604800
@       IN      SOA     bnehyp02.dev.randomnoun. root.dev.randomnoun. (
         2022123001     ; (YYYYMMDDNN) Serial INCREMENT THIS EVERY TIME FILE CHANGES
                                        ; (triggers zone transfer if secondary configured)
                         604800         ; Refresh
                          86400         ; Retry
                        2419200         ; Expire
                          10800 )       ; Negative Cache TTL
;
@       IN      NS      bnehyp02.dev.randomnoun.
@       IN      A       192.168.0.12
;@      IN      AAAA    ::1

; hypers
bnehyp02    IN  A   192.168.0.24
bnehyp05    IN  A   192.168.0.112

; other physical machines/devices
bnenas04    IN  A   192.168.0.129
excimer     IN  A   192.168.0.125
excimer-wifi  IN A  192.168.0.126

; bnehyp05 VMs
bnekub02    IN  A   192.168.0.130
bnenod03    IN  A   192.168.0.131
bnesql02    IN  A   192.168.0.132

; aliases
mysql       IN  CNAME bnesql02
vault       IN  CNAME bnesql02

; bnekub02 containers
docker.nexus3  IN CNAME bnenod03
docker-combined.nexus3  IN CNAME bnenod03
docker-releases.nexus3  IN CNAME bnenod03
docker-snapshots.nexus3  IN CNAME bnenod03
gitlab         IN CNAME bnenod03
nexus2         IN CNAME bnenod03
nexus3         IN CNAME bnenod03
```

* Create the file  `/etc/bind/db.192` containing:

```
;
; BIND reverse data file for dev.randomnoun zone
;
$TTL    604800
@       IN      SOA     bnehyp02.dev.randomnoun. root.dev.randomnoun. (
             2012030501         ; Serial
                         604800         ; Refresh
                          86400         ; Retry
                        2419200         ; Expire
                         604800 )       ; Negative Cache TTL
;
@       IN      NS      bnehyp02.
24.0.168   IN  PTR  bnehyp02
125.0.168  IN  PTR  excimer
126.0.168  IN  PTR  excimer-wifi
129.0.168  IN  PTR  bnenas04
130.0.168  IN  PTR  bnekub02
131.0.168  IN  PTR  bnenod03
132.0.168  IN  PTR  bnesql02
```

* Restart bind via `sudo service bind9 restart`
* Check status via `sudo service bind9 status`
* Fix up any errors that might be appearing in `/var/log/syslog`

# Seems to be some gaps in your IP addresses there

Well yes, I've had the bookcase for a while, and this is not the entire list of crap I've got connecting to this network.


