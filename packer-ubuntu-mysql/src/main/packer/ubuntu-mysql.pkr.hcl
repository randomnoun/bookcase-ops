variable "esxi_host" { type = string }
variable "esxi_datastore" { type = string }
variable "esxi_username" { type = string }
variable "esxi_password" { type = string }

variable "cloud_init_username" { type = string }
variable "cloud_init_fullname" { type = string }
variable "cloud_init_password" { type = string }
variable "cloud_init_password_hash" { type = string }

variable "builder_hostname" { type = string }
variable "builder_numvpus" { type = string }
variable "builder_numcores" { type = string }
variable "builder_memsize" { type = string }
variable "builder_disksize" { type = string }
variable "builder_ethernet0_mac" { type = string }

variable "mysql_root_password" { type = string }

variable "backup_host" { type = string }
variable "backup_path" { type = string }
variable "backup_username" { type = string }
variable "backup_password" { type = string }

source "vmware-iso" "mysql" {

  vm_name                = "${var.builder_hostname}"
  guest_os_type          = "ubuntu-64"
  // missing version
  headless               = false
  format                 = "ova"

  cpus                   = "${var.builder_numvpus}"
  cores                  = "${var.builder_numcores}"
  memory                 = "${var.builder_memsize}"
  sound                  = "true"

  // missing sound
  disk_type_id           = "thin"
  vmx_data = {
    "ethernet0.networkName" = "VM Network"
    "tools.syncTime"        = "0"
    
    "ethernet0.addressType" = "static"
    "ethernet0.address"     = "${var.builder_ethernet0_mac}"
    
  }
  disk_size              = "${var.builder_disksize}"  
  

  iso_url                = "https://releases.ubuntu.com/22.04/ubuntu-22.04.1-live-server-amd64.iso"
  iso_checksum           = "10f19c5b2b8d6db711582e0e27f5116296c34fe4b313ba45f9b201a5007056cb"
  output_directory  = "target/build"
  snapshot_name     = "clean"  
  
  
  http_directory         = "builder-http"
  ssh_username           = "${var.cloud_init_username}"
  ssh_password           = "${var.cloud_init_password}"
  shutdown_command       = "echo '${var.cloud_init_password}' | /usr/bin/sudo -E -S shutdown -P now"

  boot_key_interval = "10ms" 
  boot_wait              = "5s"
  boot_command           = [
    "c<wait>", 
    "linux /casper/vmlinuz --- autoinstall ds=\"nocloud-net;seedfrom=http://{{.HTTPIP}}:{{.HTTPPort}}/\"", 
    "<enter><wait>", 
    "initrd /casper/initrd", 
    "<enter><wait>", 
    "boot", 
    "<enter>"
  ]
  skip_export            = true
  
  
  remote_type            = "esx5"
  remote_host            = "${var.esxi_host}"
  remote_datastore       = "${var.esxi_datastore}"
  remote_username        = "${var.esxi_username}"
  remote_password        = "${var.esxi_password}"
  remote_cache_datastore = "datastore1"
  remote_cache_directory = "packer_cache"
  


// this is all extra
  keep_registered        = true
  ssh_handshake_attempts = "20"
  ssh_pty                = true
  ssh_timeout            = "20m"
  tools_upload_flavor    = "linux"
  vnc_disable_password = true
}

build {
  sources = ["source.vmware-iso.mysql"]

  // directory needs to already exist for the file provisioner to recursively copy a directory  
  provisioner "shell" {
    inline      = [ 
       "mkdir -p /opt/packer",
       "chmod 777 /opt/packer"
    ]
    execute_command  = "echo '${var.cloud_init_password}' | {{ .Vars }} sudo -E -S /bin/bash '{{ .Path }}'"
  }

  provisioner "shell" {
    environment_vars = [
      "BACKUP_HOST=${var.backup_host}", 
      "BACKUP_PATH=${var.backup_path}", 
      "BACKUP_USERNAME=${var.backup_username}", 
      "BACKUP_PASSWORD=${var.backup_password}", 
      "MYSQL_ROOT_PASSWORD=${var.mysql_root_password}"
    ]
    execute_command  = "echo '${var.cloud_init_password}' | {{ .Vars }} sudo -E -S /bin/bash '{{ .Path }}'"
    script           = "packer-scripts/01-install.sh"
  }

}
