#!/bin/bash
set -e

ESXI_HOST="192.168.4.90"
SSH_OPTS="-oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa"
SCP_OPTS="-c aes128-ctr $SSH_OPTS"
TARGET="/mnt/vm-storage/VM/er-testbed"
DS="/vmfs/volumes/datastore1/ER-TB"

mkdir -p "$TARGET"

copy_and_rename() {
    local remote_dir="$1"
    local remote_file="$2"
    local local_name="$3"

    echo "[+] Copying $remote_file from $remote_dir..."
    scp $SCP_OPTS \
        "root@${ESXI_HOST}:${remote_dir}/${remote_file}.vmdk" \
        "root@${ESXI_HOST}:${remote_dir}/${remote_file}-flat.vmdk" \
        "$TARGET/"

    mv "$TARGET/${remote_file}.vmdk" "$TARGET/${local_name}-src.vmdk"
    mv "$TARGET/${remote_file}-flat.vmdk" "$TARGET/${local_name}-src-flat.vmdk"
    sed -i "s/${remote_file}-flat.vmdk/${local_name}-src-flat.vmdk/" "$TARGET/${local_name}-src.vmdk"
    echo "[+] Renamed to ${local_name}-src.vmdk / ${local_name}-src-flat.vmdk"
}

# All 5 share identical source filename IPv6_RefDUT_2.vmdk — rename immediately each time
copy_and_rename "$DS/Host-1" "IPv6_RefDUT_2" "Host-A"
copy_and_rename "$DS/Host-2" "IPv6_RefDUT_2" "Host-B"
copy_and_rename "$DS/Manager" "IPv6_RefDUT_2" "IR"
copy_and_rename "$DS/Router-1" "IPv6_RefDUT_2" "Router-A"
copy_and_rename "$DS/Router-3" "IPv6_RefDUT_2" "Router-C"

echo "[+] All copies complete. Converting to qcow2..."

for vm in Host-A Host-B IR Router-A Router-C; do
    echo "[+] Converting $vm..."
    qemu-img convert -f vmdk -O qcow2 "$TARGET/${vm}-src.vmdk" "$TARGET/${vm}.qcow2"
done

echo "[+] Cleaning up raw vmdk/flat copies..."
rm -f "$TARGET"/*-src.vmdk "$TARGET"/*-src-flat.vmdk

echo "[+] Done. qcow2 files ready in $TARGET"
ls -la "$TARGET"

# ─── Phase 2: Proxmox Registration ───────────────────────────────────────────
POOL="ER-Test-Bed"
STORAGE="vm-storage"
NODE="proxmax"

declare -A ER_VMS=(
  [501]="Host-A"
  [502]="Host-B"
  [503]="IR"
  [504]="Router-A"
  [505]="Router-C"
)

echo "[+] Creating pool $POOL (if not exists)..."
pvesh create /pools --poolid "$POOL" 2>/dev/null || true

for VMID in "${!ER_VMS[@]}"; do
  NAME="${ER_VMS[$VMID]}"
  QCOW2="$TARGET/${NAME}.qcow2"
  SAFE_NAME="${NAME//_/-}"

  echo ""
  echo "=== [$VMID] $NAME ==="

  # Idempotent — skip if already registered
  if qm config "$VMID" &>/dev/null; then
    echo "[SKIP] VMID $VMID already exists in Proxmox"
    continue
  fi

  if [ ! -f "$QCOW2" ]; then
    echo "[ERROR] $QCOW2 not found — skipping"
    continue
  fi

  echo "[+] Creating VM..."
  qm create "$VMID" \
    --name "$SAFE_NAME" \
    --memory 2048 \
    --cores 2 \
    --ostype l26 \
    --net0 virtio,bridge=vmbr0 \
    --serial0 socket \
    --vga serial0 \
    --description "Original ESXi name: ${NAME} | Testbed: ER-Test-Bed | Migrated: $(date +%Y-%m-%d)"

  echo "[+] Importing disk..."
  qm importdisk "$VMID" "$QCOW2" "$STORAGE"

  echo "[+] Attaching disk and setting boot order..."
  qm set "$VMID" \
    --sata0 "${STORAGE}:vm-${VMID}-disk-0" \
    --boot order=sata0

  echo "[+] Assigning to pool $POOL..."
  pvesh set /pools/"$POOL" -vms "$VMID"

  echo "[✓] VMID $VMID ($NAME) registered successfully"
done

echo ""
echo "=== ER-Test-Bed Migration Complete ==="
echo "--- Pool members ---"
pvesh get /pools/"$POOL" --output-format json | python3 -c "
import sys,json
d=json.load(sys.stdin)
for m in d.get('members',[]):
    print(f\"  VMID {m['vmid']} : {m.get('name','?')} [{m.get('type','?')}]\")"

echo "--- VM list ---"
qm list | grep -E "^( *(50[0-9]))"
