variable "proxmox_url" { type = string }
variable "proxmox_username" { type = string }
variable "proxmox_token" { type = string }
variable "proxmox_node" { type = string }
variable "proxmox_iso_storage_pool" { type = string }
variable "proxmox_disk_storage_pool" { type = string }
variable "proxmox_efi_storage_pool" { type = string }
variable "proxmox_bridge" { type = string }
variable "packer_http_bind_address" { type = string }
variable "proxmox_insecure_skip_tls_verify" { type = bool }

variable "cloud_init_username" { type = string }
variable "cloud_init_fullname" { type = string }
variable "cloud_init_password" { type = string }
variable "cloud_init_password_hash" { type = string }
variable "cloud_init_authorized_keys" { type = string }

variable "template_name" { type = string }
variable "template_vmid" { type = number }

variable "builder_hostname" { type = string }
variable "builder_numvpus" { type = string }
variable "builder_numcores" { type = string }
variable "builder_memsize" { type = string }
variable "builder_disksize" { type = string }
variable "builder_ethernet0_mac" { type = string }

variable "backup_host" { type = string }
variable "backup_path" { type = string }
variable "backup_username" { type = string }
variable "backup_password" { type = string }

packer {
  required_version = ">= 1.7.0"
  required_plugins {
    proxmox = {
      version = ">= 1.1.0"
      source  = "github.com/hashicorp/proxmox"
    }
  }
}

source "proxmox-iso" "kubernetes-node" {

  proxmox_url              = "${var.proxmox_url}"
  username                 = "${var.proxmox_username}"
  token                    = "${var.proxmox_token}"
  insecure_skip_tls_verify = "${var.proxmox_insecure_skip_tls_verify}"
  node                     = "${var.proxmox_node}"

  // default (1m) isn't enough for the node to pull down the ~3GB ubuntu iso
  // via iso_download_pve
  task_timeout = "15m"

  // vm_name is only used while packer is building (shown in the proxmox UI,
  // and used as the ssh hostname via cloud-init); template_name is what the
  // finished template is actually renamed to once conversion completes, and
  // vm_id pins it to a predictable, high (easy to spot) id rather than
  // whatever the next free one happens to be. Deliberately decoupled from
  // builder_hostname so clones can be renamed independently of the template.
  vm_name       = "${var.builder_hostname}"
  template_name = "${var.template_name}"
  vm_id         = "${var.template_vmid}"
  os            = "l26"

  // q35 + UEFI gives a modern PCIe bus, needed to pass through the RTX 3090s
  // after cloning this template (never during the packer build itself - see
  // the note on pci_devices below).
  machine  = "q35"
  bios     = "ovmf"
  cpu_type = "host"

  efi_config {
    efi_storage_pool  = "${var.proxmox_efi_storage_pool}"
    efi_type          = "4m"
    pre_enrolled_keys = false
  }

  sockets = "${var.builder_numvpus}"
  cores   = "${var.builder_numcores}"
  memory  = "${var.builder_memsize}"

  qemu_agent = true

  // Deliberately no pci_devices block here: attaching the physical GPUs
  // while packer is building would lock the cards and break provisioning.
  // Attach the 3090s to the cloned VM afterwards instead.

  scsi_controller = "virtio-scsi-pci"
  disks {
    type         = "scsi"
    disk_size    = "${var.builder_disksize}M"
    storage_pool = "${var.proxmox_disk_storage_pool}"
    format       = "raw"
  }

  network_adapters {
    model       = "virtio"
    bridge      = "${var.proxmox_bridge}"
    mac_address = "${var.builder_ethernet0_mac}"
  }

  boot_iso {
    iso_url          = "https://releases.ubuntu.com/noble/ubuntu-24.04.3-live-server-amd64.iso"
    iso_checksum     = "c3514bf0056180d09376462a7a1b4f213c1d6e8ea67fae5c25099c6fd3d8274b"
    iso_storage_pool = "${var.proxmox_iso_storage_pool}"
    unmount          = true
    // have the proxmox node fetch the iso itself; packer download+re-upload
    // via the API hits pveproxy's request body size limit on a ~3GB iso
    iso_download_pve = true
  }

  http_directory = "../common/builder-http"
  // pin this explicitly - on a machine with a VPN TAP adapter (or other
  // virtual NICs) packer can autodetect the wrong interface for {{.HTTPIP}},
  // handing the VM a seed URL it can never reach
  http_bind_address = "${var.packer_http_bind_address}"
  ssh_username      = "${var.cloud_init_username}"
  ssh_password      = "${var.cloud_init_password}"

  // proxmox's qemu/vnc key injection is slower and drops keystrokes more
  // easily than esxi's console did at these settings, so give it more time
  // to settle between keypresses
  boot_key_interval = "50ms"
  boot_wait         = "10s"
  boot_command = [
    "c<wait3s>",
    "linux /casper/vmlinuz --- autoinstall ds=\"nocloud-net;seedfrom=http://{{.HTTPIP}}:{{.HTTPPort}}/\"",
    "<enter><wait>",
    "initrd /casper/initrd",
    "<enter><wait>",
    "boot",
    "<enter>"
  ]

  ssh_handshake_attempts = "20"
  ssh_pty                = true
  ssh_timeout            = "20m"
}

build {
  sources = ["source.proxmox-iso.kubernetes-node"]

  // directory needs to already exist for the file provisioner to recursively copy a directory
  provisioner "shell" {
    inline = [
      "mkdir -p /opt/packer",
      "chmod 777 /opt/packer"
    ]
    execute_command = "echo '${var.cloud_init_password}' | {{ .Vars }} sudo -E -S /bin/bash '{{ .Path }}'"
  }

  provisioner "file" {
    source      = "../common/filesystem/"
    destination = "/opt/packer"
  }

  provisioner "shell" {
    environment_vars = [
      "CLOUD_INIT_USERNAME=${var.cloud_init_username}",
      "BACKUP_HOST=${var.backup_host}",
      "BACKUP_PATH=${var.backup_path}",
      "BACKUP_USERNAME=${var.backup_username}",
      "BACKUP_PASSWORD=${var.backup_password}",
    ]
    execute_command = "echo '${var.cloud_init_password}' | {{ .Vars }} sudo -E -S /bin/bash '{{ .Path }}'"
    script          = "../common/packer-scripts/01-install.sh"
  }

  provisioner "shell" {
    execute_command = "echo '${var.cloud_init_password}' | {{ .Vars }} sudo -E -S /bin/bash '{{ .Path }}'"
    script          = "packer-scripts/02-install-nvidia.sh"
  }

}
