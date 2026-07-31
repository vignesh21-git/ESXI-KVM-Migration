#!/bin/bash
set -e

TARGET="/mnt/vm-storage/VM/ip-security"
POOL="IP-Security"

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

register_vm 800 "VAPT-DUT-SCTP" "VAPT-DUT-SCTP.qcow2" 2048 2 \
    "Original ESXi name: VAPT_DUT-SCTP (REF-R2-18) (VMID 36, source host: 192.168.3.90, path: VAPT_REF_DUT/IPv6 Ref DUT 20.04.2.vmdk)"

register_vm 801 "Linux-Client" "Linux-Client.qcow2" 2048 1 \
    "Original ESXi name: Linux_Client (TB1-R2-06) (VMID 38, source host: 192.168.3.90, path: Linux_Client/Linux_Client.vmdk)"

register_vm 802 "Linux-Server" "Linux-Server.qcow2" 2048 1 \
    "Original ESXi name: Linux_Server (TB1-R2-07) (VMID 39, source host: 192.168.3.90, path: Linux_Server/Linux_Server.vmdk)"

register_vm 803 "Linux-Peer" "Linux-Peer.qcow2" 2048 1 \
    "Original ESXi name: Linux_Peer (VMID 40, source host: 192.168.3.90, path: Linux_Peer/Linux_Client.vmdk)"

register_vm 804 "Server-Ubuntu" "Server-Ubuntu.qcow2" 2048 1 \
    "Original ESXi name: Server-Ubuntu (TB1-R2-09) (VMID 44, source host: 192.168.3.90, path: IPSecEq-Ubuntu/Server-Ubuntu.vmdk)"

register_vm 805 "Client-Kali" "Client-Kali.qcow2" 4096 2 \
    "Original ESXi name: Client-Kali (TB1-R2-08) (VMID 46, source host: 192.168.3.90, path: Client-Kali/Client-Kali.vmdk)"

register_vm 806 "TB-Telemetry-Server" "TB-Telemetry-Server.qcow2" 2048 1 \
    "Original ESXi name: TB_Telemetry_Server (VMID 119, source host: 192.168.3.90, path: Telemetry_Consumer/Telemetry.vmdk)"

register_vm 807 "TB-Telemetry-Sensor" "TB-Telemetry-Sensor.qcow2" 2048 1 \
    "Original ESXi name: TB_Telemetry_Sensor (VMID 120, source host: 192.168.3.90, path: Telemetry_Producer/Telemetry.vmdk)"

register_vm 808 "UTM-Linux-Server" "UTM-Linux-Server.qcow2" 2048 1 \
    "Original ESXi name: UTM_Linux_Server (TB1-R2-12) (VMID 123, source host: 192.168.3.90, path: Linux1/Peer_New.vmdk)"

register_vm 809 "Peer-Pfsense" "Peer-Pfsense.qcow2" 2048 1 \
    "Original ESXi name: Peer_Pfsense (TB1-R2-10) (VMID 126, source host: 192.168.3.90, path: Pfsense/Pfsense.vmdk)"

register_vm 810 "UTM-Linux-Client" "UTM-Linux-Client.qcow2" 2048 1 \
    "Original ESXi name: UTM_Linux_Client (TB1-R2-11) (VMID 82, source host: 192.168.3.90, path: TB_PPPoE_IPsec/Linux1/Peer_New.vmdk)"

register_vm 811 "NetconfServer" "NetconfServer.qcow2" 2048 1 \
    "Original ESXi name: NetconfServer (TB1-R2-20) (VMID 88, source host: 192.168.3.90, path: NetconfServer/NetconfServer.vmdk)"

echo "===== IP-Security registration complete ====="
qm list | grep -E "80[0-9]|81[01]"
