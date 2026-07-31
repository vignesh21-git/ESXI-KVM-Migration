#!/bin/bash
set -e

ESXI_HOST="192.168.3.90"
SSH_OPTS="-oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa"
SCP_OPTS="-c aes128-ctr $SSH_OPTS"
TARGET="/mnt/vm-storage/VM/ip-security"
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

# 1. VAPT_DUT-SCTP — has spaces, handle explicitly
echo "[+] Copying VAPT_DUT-SCTP (contains space)..."
scp $SCP_OPTS \
    "root@${ESXI_HOST}:${DS}/VAPT_REF_DUT/IPv6 Ref DUT 20.04.2.vmdk" \
    "root@${ESXI_HOST}:${DS}/VAPT_REF_DUT/IPv6 Ref DUT 20.04.2-flat.vmdk" \
    "$TARGET/"
mv "$TARGET/IPv6 Ref DUT 20.04.2.vmdk" "$TARGET/VAPT-DUT-SCTP-src.vmdk"
mv "$TARGET/IPv6 Ref DUT 20.04.2-flat.vmdk" "$TARGET/VAPT-DUT-SCTP-src-flat.vmdk"
sed -i "s/IPv6 Ref DUT 20.04.2-flat.vmdk/VAPT-DUT-SCTP-src-flat.vmdk/" "$TARGET/VAPT-DUT-SCTP-src.vmdk"

# 2. Linux_Client
copy_and_rename "$DS/Linux_Client" "Linux_Client" "Linux-Client"

# 3. Linux_Server
copy_and_rename "$DS/Linux_Server" "Linux_Server" "Linux-Server"

# 4. Linux_Peer — collides with #2's filename
copy_and_rename "$DS/Linux_Peer" "Linux_Client" "Linux-Peer"

# 5. Server-Ubuntu
copy_and_rename "$DS/IPSecEq-Ubuntu" "Server-Ubuntu" "Server-Ubuntu"

# 6. Client-Kali
copy_and_rename "$DS/Client-Kali" "Client-Kali" "Client-Kali"

# 7. TB_Telemetry_Server
copy_and_rename "$DS/Telemetry_Consumer" "Telemetry" "TB-Telemetry-Server"

# 8. TB_Telemetry_Sensor — collides with #7's filename
copy_and_rename "$DS/Telemetry_Producer" "Telemetry" "TB-Telemetry-Sensor"

# 9. UTM_Linux_Server
copy_and_rename "$DS/Linux1" "Peer_New" "UTM-Linux-Server"

# 10. Peer_Pfsense
copy_and_rename "$DS/Pfsense" "Pfsense" "Peer-Pfsense"

# 11. UTM_Linux_Client — collides with #9's filename
copy_and_rename "$DS/TB_PPPoE_IPsec/Linux1" "Peer_New" "UTM-Linux-Client"

# 12. NetconfServer
copy_and_rename "$DS/NetconfServer" "NetconfServer" "NetconfServer"

echo "[+] All copies complete. Converting to qcow2..."

for vm in VAPT-DUT-SCTP Linux-Client Linux-Server Linux-Peer Server-Ubuntu \
          Client-Kali TB-Telemetry-Server TB-Telemetry-Sensor \
          UTM-Linux-Server Peer-Pfsense UTM-Linux-Client NetconfServer; do
    echo "[+] Converting $vm..."
    qemu-img convert -f vmdk -O qcow2 "$TARGET/${vm}-src.vmdk" "$TARGET/${vm}.qcow2"
done

echo "[+] Cleaning up raw vmdk/flat copies..."
rm -f "$TARGET"/*-src.vmdk "$TARGET"/*-src-flat.vmdk

echo "[+] Done. qcow2 files ready in $TARGET"
ls -la "$TARGET"
