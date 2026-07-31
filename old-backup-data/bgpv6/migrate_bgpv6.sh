#!/bin/bash
set -e

ESXI_HOST="192.168.4.90"
SSH_OPTS="-oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa"
SCP_OPTS="-c aes128-ctr $SSH_OPTS"
TARGET="/mnt/vm-storage/VM/bgpv6"
DS="/vmfs/volumes/Data_Storage/BGPv6"

mkdir -p "$TARGET"

copy_and_rename() {
    local remote_dir="$1"
    local remote_file="$2"   # without extension
    local local_name="$3"    # target local name, without extension

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

# Class A (clean Linux)
copy_and_rename "$DS/Debian-BGPV6" "Debian-BGPV6" "Debian-BGPv6"
copy_and_rename "$DS/Fedora-V6" "Fedora-V6" "Fedora-BGPv6"

# Ubuntu-BGPv6 — has spaces, handle explicitly
echo "[+] Copying Ubuntu-BGPv6 (Ref DUT, contains spaces)..."
scp $SCP_OPTS \
    "root@${ESXI_HOST}:${DS}/BGPv6_REFDUT-Router/IPv6 Ref DUT 20.04.2.vmdk" \
    "root@${ESXI_HOST}:${DS}/BGPv6_REFDUT-Router/IPv6 Ref DUT 20.04.2-flat.vmdk" \
    "$TARGET/"
mv "$TARGET/IPv6 Ref DUT 20.04.2.vmdk" "$TARGET/Ubuntu-BGPv6-src.vmdk"
mv "$TARGET/IPv6 Ref DUT 20.04.2-flat.vmdk" "$TARGET/Ubuntu-BGPv6-src-flat.vmdk"
sed -i "s/IPv6 Ref DUT 20.04.2-flat.vmdk/Ubuntu-BGPv6-src-flat.vmdk/" "$TARGET/Ubuntu-BGPv6-src.vmdk"

# Class C (FreeBSD)
copy_and_rename "$DS/FreeBSD-BGPv6" "FreeBSD-BGPv6" "FreeBSD-BGPv6"
copy_and_rename "$DS/IPv6_Dumper" "DUMPER" "BGPv6-Dumper"

# Class E (Windows) — ALL FOUR share identical source filenames, rename immediately each time
copy_and_rename "$DS/IPv6_WIN10-HOST" "IPv6_WIN10-HOST" "BGPv6-WIN10-HOST-U"
copy_and_rename "$DS/Host-2" "IPv6_WIN10-HOST" "BGPv6-WIN10-HOST-F"
copy_and_rename "$DS/Host-3" "IPv6_WIN10-HOST" "BGPv6-WIN10-HOST-Fr"
copy_and_rename "$DS/Host4" "IPv6_WIN10-HOST" "BGPv6-WIN10-HOST-D"

echo "[+] All copies complete. Converting to qcow2..."

for vm in Debian-BGPv6 Fedora-BGPv6 Ubuntu-BGPv6 FreeBSD-BGPv6 BGPv6-Dumper \
          BGPv6-WIN10-HOST-U BGPv6-WIN10-HOST-F BGPv6-WIN10-HOST-Fr BGPv6-WIN10-HOST-D; do
    echo "[+] Converting $vm..."
    qemu-img convert -f vmdk -O qcow2 "$TARGET/${vm}-src.vmdk" "$TARGET/${vm}.qcow2"
done

echo "[+] Cleaning up raw vmdk/flat copies..."
rm -f "$TARGET"/*-src.vmdk "$TARGET"/*-src-flat.vmdk

echo "[+] Done. qcow2 files ready in $TARGET"
ls -la "$TARGET"
