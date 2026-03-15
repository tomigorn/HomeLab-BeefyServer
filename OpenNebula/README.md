
# OpenNebula POC using VMs as Nodes

This repository contains notes and a setup guide for a proof-of-concept OpenNebula deployment that uses virtual machines as the compute "nodes". I don't have enough physical servers in my homelab to run a full bare-metal OpenNebula cluster, so this POC uses powerful VMs on an Ubuntu host to emulate the real servers. Most of the configuration and behaviour will match a real deployment and lets me validate orchestration, networking, storage and lifecycle operations before rolling out to physical hardware.

## Why use VMs as nodes
- Hardware shortage: no money for multiple bare-metal machines available for testing.
- Faster iteration: VMs are quick to provision, snapshot and revert.
- Feature parity: nested virtualization + proper passthrough (SR-IOV / VFIO) reproduces almost all real-server behaviors.
- Safety: tests won't risk a production physical server.

Tradeoffs: nested virtualization adds some overhead and limits certain passthrough scenarios. For high-performance networking or GPUs, prefer SR-IOV or direct passthrough. Where possible test with a mix of VM nodes and one or two physical nodes.

## High-level approach
1. Prepare the Ubuntu host: enable CPU virtualization and IOMMU, install KVM/QEMU/libvirt.
2. Configure the host for high-performance VMs (hugepages, CPU pinning, NUMA awareness, fast storage).
3. Enable nested virtualization so each VM can run libvirt/kvm inside.
4. Build a VM template (cloud-init + virt-install or qcow2) optimized for OpenNebula node duties.
5. Install OpenNebula Node inside each VM and register the nodes with the OpenNebula front-end.
6. Validate with migrations, nested VM creation, and benchmarks.

## Prerequisites
- Ubuntu server with virtualization-capable CPU (Intel VT-x or AMD SVM).
- Enough RAM/CPU to run several large VMs.
- NVMe or SSD storage for good VM performance.

## Quick checks on host
Check CPU virtualization support.

```bash
# First update apt.
$ sudo apt update
[sudo] password for buntu: 
Hit:1 http://ch.archive.ubuntu.com/ubuntu noble InRelease
Hit:2 http://ch.archive.ubuntu.com/ubuntu noble-updates InRelease                                       
Hit:3 http://ch.archive.ubuntu.com/ubuntu noble-backports InRelease                                     
Hit:4 https://download.docker.com/linux/ubuntu noble InRelease                                          
Hit:5 http://security.ubuntu.com/ubuntu noble-security InRelease           
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
2 packages can be upgraded. Run 'apt list --upgradable' to see them.
```

```bash
# check number of CPU Cores
$ nproc
12
# check number of CPUs (=threads) reporting VT-x/SVM support (virtualization)
$ egrep -c '(vmx|svm)' /proc/cpuinfo
24
```

```bash
# Check whether libvirt's `virsh` CLI is available (prints version when installed).
$ virsh --version || true
Command 'virsh' not found, but can be installed with:
sudo apt install libvirt-clients

# Check whether the QEMU system binary is installed (used to run virtual machines).
$ which qemu-system-x86_64 || true
# prints nothing, isn't installed
```

```bash
# Install KVM, libvirt and tools
$ sudo apt update
$ sudo apt install -y qemu-kvm libvirt-daemon-system libvirt-clients bridge-utils virtinst cloud-image-utils
[... lots of output before: all installed and reboot might be needed]

# Add your user to the libvirt/kvm groups so you can manage VMs without sudo
$ sudo usermod -aG libvirt,kvm $USER

# Enable and start libvirt
$ sudo systemctl enable --now libvirtd

# Re-run the checks
$ virsh --version || true
10.0.0
$ which qemu-system-x86_64 || true
/usr/bin/qemu-system-x86_64
```

now we know it is installed and we can continue

## Enable IOMMU (for passthrough) and update GRUB
> we would do this in prod for full hardware passthrough. for this poc I will skip IOMMU. Continue with Nester Virtualization

Edit `/etc/default/grub` and add the appropriate kernel option to `GRUB_CMDLINE_LINUX`:

- Intel: `intel_iommu=on`
- AMD: `amd_iommu=on`

Then update grub and reboot:

```bash
sudo update-grub
sudo reboot
```

## Enable nested virtualization

```bash
# Remove and reload the Intel KVM module with nested enabled (no output expected)
$ sudo modprobe -r kvm_intel
$ sudo modprobe kvm_intel nested=1

# Persist the option so it survives reboots
$ echo 'options kvm_intel nested=1' | sudo tee /etc/modprobe.d/kvm.conf
options kvm_intel nested=1

# Verify nested is enabled (expect: Y)
$ cat /sys/module/kvm_intel/parameters/nested
Y

# Confirm KVM modules are loaded
$ lsmod | grep kvm
kvm_intel             487424  0
kvm                  1409024  1 kvm_intel
irqbypass              12288  1 kvm

# dmesg may require sudo; absence of output is normal when nothing notable logged
$ dmesg | tail -n 40 | grep -i kvm
dmesg: read kernel buffer failed: Operation not permitted
$ sudo dmesg | tail -n 40 | grep -i kvm
# (no output)

# Check host exposes virtualization to guests (non-zero means guests can see VT-x/SVM)
$ egrep -c '(vmx|svm)' /proc/cpuinfo
24
```

Result: nested virtualization is enabled on the host and the setting is persisted.

For AMD hosts, replace `kvm_intel` with `kvm_amd` in the commands above.

## Performance tuning on host

Performance tuning reduces the virtualization overhead and helps the VM nodes behave closer to bare-metal. Tuning CPU placement, memory (hugepages), and storage/IO paths improves latency, throughput and migration stability — important for nested virtualization and for running production-like workloads inside the POC VMs.

- Hugepages (example):

```bash
# Set hugepages at runtime (applies immediately but NOT persistent across reboots)
$ sudo sysctl -w vm.nr_hugepages=1024
vm.nr_hugepages = 1024

# Make the setting persistent across reboots
$ echo 'vm.nr_hugepages=1024' | sudo tee -a /etc/sysctl.conf
vm.nr_hugepages=1024
$ sudo sysctl -p
vm.nr_hugepages = 1024
```

### Other Tuning Recommendations which i will not apply for the poc
- CPU pinning and NUMA: plan CPU sets and pin VM vCPUs to physical cores when creating VMs (use `virsh vcpupin` or virt-install flags).
- Storage: use raw LVM or direct NVMe-backed images for best throughput. Prefer `virtio-blk`/`virtio-scsi` drivers for guests.

## SR-IOV / PCIe / GPU passthrough (notes)
- To provide near-bare-metal network/GPU performance, use SR-IOV or VFIO passthrough. This requires IOMMU enabled and careful IOMMU group inspection.
- Example: create virtual functions on a NIC:

```bash
echo 8 | sudo tee /sys/class/net/eth0/device/sriov_numvfs
```

- Bind a PF/VF to `vfio-pci` after identifying device IDs and IOMMU groups. See vendor docs and `lspci -nnk`.

## Create an OpenNebula-ready VM template
Use cloud-init to provision the VM with SSH keys, package installs and basic config. Example `user-data` (cloud-init):

```yaml
#cloud-config
disable_root: false
ssh_pwauth: false
users:
	- name: ubuntu
		sudo: ALL=(ALL) NOPASSWD:ALL
		groups: users, admin
		shell: /bin/bash
		ssh_authorized_keys:
			- <your-public-ssh-key-here>
packages:
	- qemu-guest-agent
	- cloud-init
runcmd:
	- [ sh, -c, 'sudo apt update && sudo apt install -y qemu-kvm libvirt-daemon-system libvirt-clients opennebula-node' ]
```

Create an image based on Ubuntu cloud image and inject the cloud-init.

```bash
wget https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-amd64.img
cp focal-server-cloudimg-amd64.img oned-node.qcow2
cloud-localds user-data.img user-data
virt-install --name oned-node --memory 8192 --vcpus 4 --disk path=oned-node.qcow2,format=qcow2 --disk path=user-data.img,device=cdrom --import --os-type=linux --network network=default
```

After first boot, the VM should run cloud-init and install the `opennebula-node` package.

## Install OpenNebula Node inside VM
Once inside the VM (or via cloud-init), install and configure the OpenNebula node. On Ubuntu:

```bash
sudo apt update
sudo apt install -y opennebula-node
# configure /etc/one/oned.conf or point it to your front-end as needed
```

On the OpenNebula front-end, add the VM as a host (Sunstone UI or `onehost create`).

## Register VM nodes with OpenNebula front-end
- From Sunstone: Infrastructure → Hosts → Create → KVM, enter the node address and credentials.
- Or use `onehost create` from the front-end.

## Tests and validation
- Verify nested virtualization by creating a small VM inside the OpenNebula-managed node.
- Test live migration (if storage/network supports it).
- Run simple benchmarks for CPU, disk and network to compare VM-node vs bare-metal expectations.

## Automation and repeatability
- Use Ansible to automate host preparation, VM template creation, and OpenNebula node installs.
- Store a cloud-init `user-data` and a `virt-install` script or Packer template for repeatable node images.

## Notes & caveats
- Nested virtualization is suitable for development and most functional tests but expect a performance penalty vs bare-metal.
- Some passthrough scenarios (especially GPUs) can be fiddly; test early with one device.
- Keep snapshots of node VMs for quick rollback.

## References
- Example project used as inspiration: https://github.com/tomigorn/HomeLab-FastPi/tree/main/OpenNebula
- OpenNebula official docs: https://openNebula.io

## Next steps (suggested)
- Decide whether you need SR-IOV / GPU passthrough now or later.
- If you want, I can generate an Ansible playbook that prepares the host and creates a cloud-init based VM template.


