#!/bin/bash
set -e

TARGET="/mnt/vm-storage/VM/ip-conformance"
POOL="IP-Conformance"

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

    if qm config "$vmid" | grep -q "^sata0:"; then
        echo "[OK] $vmid ($name) disk confirmed attached"
    else
        echo "[!!] $vmid ($name) disk NOT attached — needs manual fix"
    fi

    pvesh set "/pools/$POOL" -vms "$vmid" 2>/dev/null || echo "[i] $vmid already in pool $POOL"
    echo "[DONE] $vmid ($name) registered"
    echo ""
}

register_vm 700 "IP-Host-1" "IP-Host-1.qcow2" 4096 1 \
    "Original ESXi name: IP_Host-1 (VMID 74, path: IP_Host/IP_Host.vmdk — had snapshot IP_Host-000002, flattened during migration)"

register_vm 701 "IP-Host-2" "IP-Host-2.qcow2" 4096 1 \
    "Original ESXi name: IP_Host-2 (VMID 75, path: IP_Host-2/IP_Host-2.vmdk)"

echo "===== IP-Conformance registration complete ====="
qm list | grep -E "700|701"
