#!/bin/bash
set -e

TARGET="/mnt/vm-storage/VM/ipv6-testbed"
POOL="IPv6-Test-Bed"

pvesh create /pools --poolid "$POOL" 2>/dev/null || echo "[i] Pool $POOL already exists"

register_vm() {
    local vmid="$1"
    local name="$2"
    local qcow2="$3"
    local mem="$4"
    local cores="$5"
    local desc="$6"

    echo "=== Registering VMID $vmid : $name ==="

    if qm status "$vmid" &>/dev/null; then
        echo "[i] VM $vmid already exists, skipping create"
    else
        qm create "$vmid" --name "$name" --memory "$mem" --cores "$cores" --net0 e1000,bridge=vmbr0
    fi

    qm importdisk "$vmid" "$TARGET/$qcow2" vm-storage
    qm set "$vmid" --sata0 "vm-storage:$vmid/vm-$vmid-disk-0.raw"
    qm set "$vmid" --boot order=sata0
    qm set "$vmid" --description "$desc"
    qm set "$vmid" --serial0 socket

    # Verify disk actually attached before moving on
    if qm config "$vmid" | grep -q "^sata0:"; then
        echo "[OK] $vmid ($name) disk confirmed attached"
    else
        echo "[!!] $vmid ($name) disk NOT attached — needs manual fix"
    fi

    pvesh set "/pools/$POOL" -vms "$vmid" 2>/dev/null || echo "[i] $vmid already in pool $POOL"
    echo "[DONE] $vmid ($name) registered"
    echo ""
}

register_vm 600 "IPv6-RefDUT-Ubuntu18" "IPv6-RefDUT-Ubuntu18.qcow2" 4096 1 \
    "Original ESXi name: IPv6_RefDUT_Ubuntu18 (VMID 80, path: Ubuntu_18_DUT/Ubuntu_18_3rd.vmdk)"

register_vm 601 "Router-Ref" "Router-Ref.qcow2" 1024 1 \
    "Original ESXi name: Router-Ref (VMID 97, path: VM -1/VM -1.vmdk)"

register_vm 602 "IPv6-Conformance" "IPv6-Conformance.qcow2" 4096 1 \
    "Original ESXi name: IPv6-Conformance (VMID 123, path: ipv6_tbcopied/IPv6-TestBed1.vmdk)"

register_vm 603 "IPv6-TestBed-Cellular" "IPv6-TestBed-Cellular.qcow2" 2048 4 \
    "Original ESXi name: IPv6-TestBed_Cellular (VMID 147, path: IPv6_Conformance/IPv6-TestBed1.vmdk)"

echo "===== IPv6 Test Bed registration complete ====="
qm list | grep -E "600|601|602|603"
