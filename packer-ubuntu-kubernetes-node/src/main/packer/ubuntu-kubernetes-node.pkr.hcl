variable "esxi_host" { type = string }
variable "esxi_datastore" { type = string }

variable "builder_hostname" { type = string }
variable "builder_numvpus" { type = string }
variable "builder_numcores" { type = string }
variable "builder_memsize" { type = string }
variable "builder_disksize" { type = string }
variable "builder_ethernet0_mac" { type = string }

variable "backup_host" { type = string }
variable "backup_path" { type = string }

// vault secrets
locals {
    esxi_username = vault("/secret/data/packer/esxi/${var.esxi_host}", "username")
    esxi_password = vault("/secret/data/packer/esxi/${var.esxi_host}", "password")
    
    cloud_init_username = vault("/secret/data/packer/cloud-init", "username")
    cloud_init_fullname = vault("/secret/data/packer/cloud-init", "fullname")
    cloud_init_password = vault("/secret/data/packer/cloud-init", "password")
    cloud_init_password_hash = vault("/secret/data/packer/cloud-init", "password-hash")
    cloud_init_authorized_keys = vault("/secret/data/packer/cloud-init", "authorized-keys")

    backup_username = vault("/secret/data/packer/backup/${var.backup_host}", "username")
    backup_password = vault("/secret/data/packer/backup/${var.backup_host}", "password")
}


source "vmware-iso" "kubernetes-node" {

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
  output_directory       = "target/build"
  snapshot_name          = "clean"  
  
  
  http_directory         = "builder-http"
  ssh_username           = "${local.cloud_init_username}"
  ssh_password           = "${local.cloud_init_password}"
  shutdown_command       = "echo '${local.cloud_init_password}' | /usr/bin/sudo -E -S shutdown -P now"

  boot_key_interval      = "10ms" 
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
  remote_username        = "${local.esxi_username}"
  remote_password        = "${local.esxi_password}"
  remote_cache_datastore = "datastore1"
  remote_cache_directory = "packer_cache"
  


// this is all extra
  keep_registered        = true
  ssh_handshake_attempts = "20"
  ssh_pty                = true
  ssh_timeout            = "20m"
  tools_upload_flavor    = "linux"
  vnc_disable_password   = true
}

build {
  sources = ["source.vmware-iso.kubernetes-node"]

  // directory needs to already exist for the file provisioner to recursively copy a directory  
  provisioner "shell" {
    inline      = [ 
       "mkdir -p /opt/packer",
       "chmod 777 /opt/packer"
    ]
    execute_command  = "echo '${local.cloud_init_password}' | {{ .Vars }} sudo -E -S /bin/bash '{{ .Path }}'"
  }
  
  provisioner "file" {
    source      = "filesystem/"
    destination = "/opt/packer"
  }

  provisioner "shell" {
    environment_vars = [
      "CLOUD_INIT_USERNAME=${local.cloud_init_username}",
      "BACKUP_HOST=${var.backup_host}",
      "BACKUP_PATH=${var.backup_path}", 
      "BACKUP_USERNAME=${local.backup_username}", 
      "BACKUP_PASSWORD=${local.backup_password}", 
    ]
    execute_command  = "echo '${local.cloud_init_password}' | {{ .Vars }} sudo -E -S /bin/bash '{{ .Path }}'"
    script           = "packer-scripts/01-install.sh"
  }

}
