#!/bin/bash
set -e

ESXI_HOST="192.168.4.90"
SSH_OPTS="-oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa"
SCP_OPTS="-c aes128-ctr $SSH_OPTS"
TARGET="/mnt/vm-storage/VM/ipv6-testbed"
DS="/vmfs/volumes/datastore1"

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

# Ubuntu_18_DUT — straightforward, unique filename
copy_and_rename "$DS/Ubuntu_18_DUT" "Ubuntu_18_3rd" "IPv6-RefDUT-Ubuntu18"

# Router-Ref — folder has a space ("VM -1"), handle explicitly
echo "[+] Copying Router-Ref (VM -1, contains space)..."
scp $SCP_OPTS \
    "root@${ESXI_HOST}:${DS}/VM -1/VM -1.vmdk" \
    "root@${ESXI_HOST}:${DS}/VM -1/VM -1-flat.vmdk" \
    "$TARGET/"
mv "$TARGET/VM -1.vmdk" "$TARGET/Router-Ref-src.vmdk"
mv "$TARGET/VM -1-flat.vmdk" "$TARGET/Router-Ref-src-flat.vmdk"
sed -i "s/VM -1-flat.vmdk/Router-Ref-src-flat.vmdk/" "$TARGET/Router-Ref-src.vmdk"

# IPv6-Conformance and IPv6-TestBed_Cellular share identical filename IPv6-TestBed1.vmdk
copy_and_rename "$DS/ipv6_tbcopied" "IPv6-TestBed1" "IPv6-Conformance"
copy_and_rename "$DS/IPv6_Conformance" "IPv6-TestBed1" "IPv6-TestBed-Cellular"

echo "[+] All copies complete. Converting to qcow2..."

for vm in IPv6-RefDUT-Ubuntu18 Router-Ref IPv6-Conformance IPv6-TestBed-Cellular; do
    echo "[+] Converting $vm..."
    qemu-img convert -f vmdk -O qcow2 "$TARGET/${vm}-src.vmdk" "$TARGET/${vm}.qcow2"
done

echo "[+] Cleaning up raw vmdk/flat copies..."
rm -f "$TARGET"/*-src.vmdk "$TARGET"/*-src-flat.vmdk

echo "[+] Done. qcow2 files ready in $TARGET"
ls -la "$TARGET"
