#!/bin/bash
set -e

ESXI_HOST="192.168.4.90"
ESXI_KEY="/root/.ssh/id_esxi.pub"
SSH_OPTS="-i $ESXI_KEY -oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa"
SCP_OPTS="-c aes128-ctr $SSH_OPTS"
TARGET="/mnt/vm-storage/VM/mpls-ldp-testbed"

mkdir -p "$TARGET"
cd "$TARGET"

echo "[+] Copying LER_1..."
scp $SCP_OPTS \
    root@${ESXI_HOST}:/vmfs/volumes/datastore1/MPLS_LDP_TESTBED/LER_1/LER_1.vmdk \
    root@${ESXI_HOST}:/vmfs/volumes/datastore1/MPLS_LDP_TESTBED/LER_1/LER_1-flat.vmdk \
    "$TARGET/"

echo "[+] Renaming LER_1 files to avoid collision with LER_2..."
mv "$TARGET/LER_1.vmdk" "$TARGET/LER-1-src.vmdk"
mv "$TARGET/LER_1-flat.vmdk" "$TARGET/LER-1-src-flat.vmdk"
sed -i 's/LER_1-flat.vmdk/LER-1-src-flat.vmdk/' "$TARGET/LER-1-src.vmdk"

echo "[+] Copying LER_2..."
scp $SCP_OPTS \
    root@${ESXI_HOST}:/vmfs/volumes/datastore1/LER2_New/LER_1.vmdk \
    root@${ESXI_HOST}:/vmfs/volumes/datastore1/LER2_New/LER_1-flat.vmdk \
    "$TARGET/"

echo "[+] Renaming LER_2 files..."
mv "$TARGET/LER_1.vmdk" "$TARGET/LER-2-src.vmdk"
mv "$TARGET/LER_1-flat.vmdk" "$TARGET/LER-2-src-flat.vmdk"
sed -i 's/LER_1-flat.vmdk/LER-2-src-flat.vmdk/' "$TARGET/LER-2-src.vmdk"

echo "[+] Copying LSR_1..."
scp $SCP_OPTS \
    root@${ESXI_HOST}:/vmfs/volumes/datastore1/MPLS_LDP_TESTBED/LSR_1/LSR.vmdk \
    root@${ESXI_HOST}:/vmfs/volumes/datastore1/MPLS_LDP_TESTBED/LSR_1/LSR-flat.vmdk \
    "$TARGET/"

echo "[+] Converting all three to qcow2..."
qemu-img convert -f vmdk -O qcow2 "$TARGET/LER-1-src.vmdk" "$TARGET/LER-1.qcow2"
qemu-img convert -f vmdk -O qcow2 "$TARGET/LER-2-src.vmdk" "$TARGET/LER-2.qcow2"
qemu-img convert -f vmdk -O qcow2 "$TARGET/LSR.vmdk" "$TARGET/LSR-1.qcow2"

echo "[+] Cleaning up raw vmdk/flat copies..."
rm -f "$TARGET/LER-1-src.vmdk" "$TARGET/LER-1-src-flat.vmdk"
rm -f "$TARGET/LER-2-src.vmdk" "$TARGET/LER-2-src-flat.vmdk"
rm -f "$TARGET/LSR.vmdk" "$TARGET/LSR-flat.vmdk"

echo "[+] Registering VMs on Proxmox..."

# LER-1 (VMID 202)
qm create 202 --name LER-1 --memory 2048 --cores 2 --net0 e1000,bridge=vmbr0
qm importdisk 202 "$TARGET/LER-1.qcow2" vm-storage
qm set 202 --sata0 vm-storage:202/vm-202-disk-0.raw
qm set 202 --boot order=sata0
qm set 202 --description "Original ESXi name: LER_1 (VMID 36, path: MPLS_LDP_TESTBED/LER_1)"
qm set 202 --serial0 socket
qm set 202 --pool MPLS-LDP-TESTBED

# LER-2 (VMID 203)
qm create 203 --name LER-2 --memory 2048 --cores 2 --net0 e1000,bridge=vmbr0
qm importdisk 203 "$TARGET/LER-2.qcow2" vm-storage
qm set 203 --sata0 vm-storage:203/vm-203-disk-0.raw
qm set 203 --boot order=sata0
qm set 203 --description "Original ESXi name: LER_2 (VMID 135, path: LER2_New — internal vmx/vmdk named LER_1)"
qm set 203 --serial0 socket
qm set 203 --pool MPLS-LDP-TESTBED

# LSR-1 (VMID 204)
qm create 204 --name LSR-1 --memory 4096 --cores 2 --net0 e1000,bridge=vmbr0
qm importdisk 204 "$TARGET/LSR-1.qcow2" vm-storage
qm set 204 --sata0 vm-storage:204/vm-204-disk-0.raw
qm set 204 --boot order=sata0
qm set 204 --description "Original ESXi name: LSR_1 (VMID 38, path: MPLS_LDP_TESTBED/LSR_1/LSR.vmdk)"
qm set 204 --serial0 socket
qm set 204 --pool MPLS-LDP-TESTBED

echo "[+] Done. Starting all three for validation..."
qm start 202
qm start 203
qm start 204

echo "[+] Migration complete. Validate via: qm terminal <vmid>"
