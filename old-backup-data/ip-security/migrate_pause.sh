cd /mnt/vm-storage/VM/ip-security

# 8. TB_Telemetry_Sensor
scp -c aes128-ctr -oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa \
    root@192.168.3.90:/vmfs/volumes/datastore1/Telemetry_Producer/Telemetry.vmdk \
    root@192.168.3.90:/vmfs/volumes/datastore1/Telemetry_Producer/Telemetry-flat.vmdk \
    /mnt/vm-storage/VM/ip-security/
mv Telemetry.vmdk TB-Telemetry-Sensor-src.vmdk
mv Telemetry-flat.vmdk TB-Telemetry-Sensor-src-flat.vmdk
sed -i 's/Telemetry-flat.vmdk/TB-Telemetry-Sensor-src-flat.vmdk/' TB-Telemetry-Sensor-src.vmdk
qemu-img convert -f vmdk -O qcow2 TB-Telemetry-Sensor-src.vmdk TB-Telemetry-Sensor.qcow2
rm -f TB-Telemetry-Sensor-src.vmdk TB-Telemetry-Sensor-src-flat.vmdk

# 9. UTM_Linux_Server
scp -c aes128-ctr -oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa \
    root@192.168.3.90:/vmfs/volumes/datastore1/Linux1/Peer_New.vmdk \
    root@192.168.3.90:/vmfs/volumes/datastore1/Linux1/Peer_New-flat.vmdk \
    /mnt/vm-storage/VM/ip-security/
mv Peer_New.vmdk UTM-Linux-Server-src.vmdk
mv Peer_New-flat.vmdk UTM-Linux-Server-src-flat.vmdk
sed -i 's/Peer_New-flat.vmdk/UTM-Linux-Server-src-flat.vmdk/' UTM-Linux-Server-src.vmdk
qemu-img convert -f vmdk -O qcow2 UTM-Linux-Server-src.vmdk UTM-Linux-Server.qcow2
rm -f UTM-Linux-Server-src.vmdk UTM-Linux-Server-src-flat.vmdk

# 10. Peer_Pfsense
scp -c aes128-ctr -oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa \
    root@192.168.3.90:/vmfs/volumes/datastore1/Pfsense/Pfsense.vmdk \
    root@192.168.3.90:/vmfs/volumes/datastore1/Pfsense/Pfsense-flat.vmdk \
    /mnt/vm-storage/VM/ip-security/
mv Pfsense.vmdk Peer-Pfsense-src.vmdk
mv Pfsense-flat.vmdk Peer-Pfsense-src-flat.vmdk
sed -i 's/Pfsense-flat.vmdk/Peer-Pfsense-src-flat.vmdk/' Peer-Pfsense-src.vmdk
qemu-img convert -f vmdk -O qcow2 Peer-Pfsense-src.vmdk Peer-Pfsense.qcow2
rm -f Peer-Pfsense-src.vmdk Peer-Pfsense-src-flat.vmdk

# 11. UTM_Linux_Client
scp -c aes128-ctr -oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa \
    root@192.168.3.90:/vmfs/volumes/datastore1/TB_PPPoE_IPsec/Linux1/Peer_New.vmdk \
    root@192.168.3.90:/vmfs/volumes/datastore1/TB_PPPoE_IPsec/Linux1/Peer_New-flat.vmdk \
    /mnt/vm-storage/VM/ip-security/
mv Peer_New.vmdk UTM-Linux-Client-src.vmdk
mv Peer_New-flat.vmdk UTM-Linux-Client-src-flat.vmdk
sed -i 's/Peer_New-flat.vmdk/UTM-Linux-Client-src-flat.vmdk/' UTM-Linux-Client-src.vmdk
qemu-img convert -f vmdk -O qcow2 UTM-Linux-Client-src.vmdk UTM-Linux-Client.qcow2
rm -f UTM-Linux-Client-src.vmdk UTM-Linux-Client-src-flat.vmdk

# 12. NetconfServer
scp -c aes128-ctr -oHostKeyAlgorithms=+ssh-rsa -oPubkeyAcceptedKeyTypes=+ssh-rsa \
    root@192.168.3.90:/vmfs/volumes/datastore1/NetconfServer/NetconfServer.vmdk \
    root@192.168.3.90:/vmfs/volumes/datastore1/NetconfServer/NetconfServer-flat.vmdk \
    /mnt/vm-storage/VM/ip-security/
mv NetconfServer.vmdk NetconfServer-src.vmdk
mv NetconfServer-flat.vmdk NetconfServer-src-flat.vmdk
sed -i 's/NetconfServer-flat.vmdk/NetconfServer-src-flat.vmdk/' NetconfServer-src.vmdk
qemu-img convert -f vmdk -O qcow2 NetconfServer-src.vmdk NetconfServer.qcow2
rm -f NetconfServer-src.vmdk NetconfServer-src-flat.vmdk
