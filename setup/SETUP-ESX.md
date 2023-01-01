# bookcase-ops ESX prerequisites

This project uses the free version of [ESXi 6.0](https://www.vmware.com/au/products/esxi-and-esx.html) 

You'll need to jump through a few hoops to get packer to be able to create virtual machines on it.

1. Enable ssh via the ESXi console
1. Enable the Guest IP Hack
    ```esxcli system settings advanced set -o /Net/GuestIPHack -i 1```
1. Enable VNC access for packer

```
chmod 644 /etc/vmware/firewall/service.xml
chmod +t /etc/vmware/firewall/service.xml
```

Then add this to /etc/vmware/firewall/service.xml
```
<service id="1000">
  <id>packer-vnc</id>
  <rule id="0000">
    <direction>inbound</direction>
    <protocol>tcp</protocol>
    <porttype>dst</porttype>
    <port>
      <begin>5900</begin>
      <end>5964</end>
    </port>
  </rule>
  <enabled>true</enabled>
  <required>true</required>
</service>
```

and restart the firewall service
```
chmod 444 /etc/vmware/firewall/service.xml
esxcli network firewall refresh
```

[Source from https://nickcharlton.net/posts/using-packer-esxi-6.html](https://nickcharlton.net/posts/using-packer-esxi-6.html).
