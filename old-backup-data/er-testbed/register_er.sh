#!/bin/bash
set -e

TARGET="/mnt/vm-storage/VM/er-testbed"
POOL="ER-Test-Bed"

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

register_vm 500 "Host-A" "Host-A.qcow2" 1024 1 \
    "Original ESXi name: Host-A (VMID 17, path: ER-TB/Host-1/IPv6_RefDUT_2.vmdk)"

register_vm 501 "IR" "IR.qcow2" 1024 1 \
    "Original ESXi name: IR (VMID 18, path: ER-TB/Manager/IPv6_RefDUT_2.vmdk)"

register_vm 502 "Router-A" "Router-A.qcow2" 1024 1 \
    "Original ESXi name: Router-A (VMID 19, path: ER-TB/Router-1/IPv6_RefDUT_2.vmdk)"

register_vm 503 "Router-C" "Router-C.qcow2" 1024 1 \
    "Original ESXi name: Router-C (VMID 29, path: ER-TB/Router-3/IPv6_RefDUT_2.vmdk)"

echo "===== ER Test Bed registration complete (Host-B deferred — VM live on ESXi) ====="
qm list | grep -E "500|501|502|503"
