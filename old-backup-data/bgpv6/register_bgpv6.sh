#!/bin/bash
TARGET=/mnt/vm-storage/VM/bgpv6
POOL=BGPv6
STORAGE=vm-storage

declare -A VMS=(
  [401]="BGPv6-Dumper"
  [402]="BGPv6-WIN10-HOST-D"
  [403]="BGPv6-WIN10-HOST-F"
  [404]="BGPv6-WIN10-HOST-Fr"
  [405]="BGPv6-WIN10-HOST-U"
  [406]="Debian-BGPv6"
  [407]="Fedora-BGPv6"
  [408]="FreeBSD-BGPv6"
  [409]="Ubuntu-BGPv6"
)

# Create pool if not exists
pvesh create /pools --poolid $POOL 2>/dev/null || true

for VMID in "${!VMS[@]}"; do
  NAME="${VMS[$VMID]}"
  QCOW2="$TARGET/${NAME}.qcow2"
  SAFE_NAME="${NAME//_/-}"

  echo "=== Registering VMID $VMID : $NAME ==="

  if [ ! -f "$QCOW2" ]; then
    echo "[SKIP] $QCOW2 not found"
    continue
  fi

  # Detect if Windows (needs SATA, no virtio)
  if [[ "$NAME" == *WIN10* ]]; then
    BUS="sata"
    OSTYPE="win10"
  else
    BUS="sata"
    OSTYPE="l26"
  fi

  qm create $VMID \
    --name "$SAFE_NAME" \
    --memory 2048 \
    --cores 2 \
    --net0 virtio,bridge=vmbr0 \
    --ostype $OSTYPE \
    --description "Original ESXi name: ${NAME} (VMID ${VMID})" \
    --serial0 socket \
    --vga serial0

  qm importdisk $VMID "$QCOW2" $STORAGE
  qm set $VMID --${BUS}0 ${STORAGE}:vm-${VMID}-disk-0 --boot order=${BUS}0

  pvesh set /pools/$POOL -vms $VMID

  echo "[DONE] $VMID ($NAME) registered and added to pool $POOL"
done

echo ""
echo "=== All done. Final state ==="
qm list | grep -E "^($(echo "${!VMS[@]}" | tr ' ' '|'))"
