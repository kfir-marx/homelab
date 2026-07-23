# Windows is intentionally a separate stack and state from the Talos cluster.
# VM 502 and Talos GPU worker 402 share largegpu's RTX 3080, so only one may
# run at a time. Terraform manages both definitions but does not toggle them.

module "windows_vms" {
  source   = "../../components/proxmox-windows-vm"
  for_each = var.windows_vms

  hostname     = each.key
  proxmox_node = each.value.proxmox_node
  vm_id        = each.value.vm_id
  cores        = each.value.cores
  memory_mb    = each.value.memory_mb
  disk_size_gb = each.value.disk_size_gb
  bridge       = var.network_bridge

  windows_iso = each.value.windows_iso
  virtio_iso  = each.value.virtio_iso

  template_vm_id = each.value.template_vm_id
  full_clone     = each.value.full_clone

  pci_devices = each.value.pci_devices
  usb_devices = each.value.usb_devices
}

output "windows_vms" {
  value = {
    for name, vm in module.windows_vms : name => {
      vm_id = vm.vm_id
      name  = vm.name
    }
  }
}
