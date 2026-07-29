"""Sanity checks for collision detection, name sanitization, and descriptor
rewriting -- using the exact real-world collision cases from the manual
migration project. Run directly: `python3 tests/test_logic.py`
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tool import collision_detector, copy_engine, register_engine
from tool.types import ApplianceRisk, DiskFile, PowerState, ResolvedPath

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


def _rp(vmid, filenames):
    return ResolvedPath(
        esxi_vmid=vmid, display_name=f"vm-{vmid}", datastore="datastore1",
        vmx_relative_path=None,
        disk_files=[
            DiskFile(relative_path=f"folder{vmid}/{fn}", role="base_descriptor" if not fn.endswith("-flat.vmdk") else "base_flat")
            for fn in filenames
        ],
        has_snapshot_chain=False, power_state=PowerState.OFF,
        appliance_risk=ApplianceRisk.NONE, appliance_reason=None,
        resolution_ok=True,
    )


# --------------------------------------------------------------------------- #
# collision_detector: real case -- 4 Windows VMs all named their disk
# IPv6_WIN10-HOST.vmdk / IPv6_WIN10-HOST-flat.vmdk in different folders
# --------------------------------------------------------------------------- #
batch = [
    _rp(402, ["IPv6_WIN10-HOST.vmdk", "IPv6_WIN10-HOST-flat.vmdk"]),
    _rp(403, ["IPv6_WIN10-HOST.vmdk", "IPv6_WIN10-HOST-flat.vmdk"]),
    _rp(404, ["IPv6_WIN10-HOST.vmdk", "IPv6_WIN10-HOST-flat.vmdk"]),
    _rp(405, ["IPv6_WIN10-HOST.vmdk", "IPv6_WIN10-HOST-flat.vmdk"]),
    _rp(406, ["Debian-BGPv6.vmdk", "Debian-BGPv6-flat.vmdk"]),  # no collision
]
report = collision_detector.detect(batch)
check("collision: detects the real 4-way IPv6_WIN10-HOST.vmdk collision", report.has_collisions)
check("collision: all 4 colliding VMIDs listed",
      report.collisions.get("IPv6_WIN10-HOST.vmdk") == [402, 403, 404, 405])
check("collision: non-colliding Debian VM not implicated",
      406 not in report.collisions.get("IPv6_WIN10-HOST.vmdk", []))
check("collision: flat file collision also detected",
      "IPv6_WIN10-HOST-flat.vmdk" in report.collisions)

no_collision_batch = [_rp(1, ["a.vmdk", "a-flat.vmdk"]), _rp(2, ["b.vmdk", "b-flat.vmdk"])]
report2 = collision_detector.detect(no_collision_batch)
check("collision: clean batch reports no collisions", not report2.has_collisions)

# --------------------------------------------------------------------------- #
# register_engine name sanitization: DNS-label validity, underscore handling
# --------------------------------------------------------------------------- #
check("sanitize: underscores become hyphens",
      register_engine.sanitize_proxmox_name("LER_2") == "LER-2")
check("sanitize: spaces and parens collapse cleanly",
      register_engine.sanitize_proxmox_name("VAPT_DUT-SCTP (REF-R2-18)") == "VAPT-DUT-SCTP-REF-R2-18")
check("sanitize: original name preserved verbatim in description",
      "LER_2" in register_engine.build_description("LER_2", 135, "LER2_New/LER_1.vmdk", "192.168.4.90"))
check("sanitize: description includes esxi vmid and host for audit trail",
      "VMID 135" in register_engine.build_description("LER_2", 135, "LER2_New/LER_1.vmdk", "192.168.4.90")
      and "192.168.4.90" in register_engine.build_description("LER_2", 135, "LER2_New/LER_1.vmdk", "192.168.4.90"))

# --------------------------------------------------------------------------- #
# copy_engine descriptor rewrite
# --------------------------------------------------------------------------- #
SAMPLE_DESCRIPTOR = """# Disk DescriptorFile
version=1
CID=fffffffe
parentCID=ffffffff
createType="vmfs"

# Extent description
RW 41943040 VMFS "LER_1-flat.vmdk"

# The Disk Data Base
#DDB
ddb.virtualHWVersion = "10"
"""

with tempfile.TemporaryDirectory() as tmp:
    desc_path = Path(tmp) / "LER-1-src.vmdk"
    desc_path.write_text(SAMPLE_DESCRIPTOR)
    rewritten = copy_engine._rewrite_descriptor_extent_reference(desc_path, "LER-1-src-flat.vmdk")
    new_text = desc_path.read_text()
    check("descriptor rewrite: reports success", rewritten)
    check("descriptor rewrite: new flat name present in extent line",
          'RW 41943040 VMFS "LER-1-src-flat.vmdk"' in new_text)
    check("descriptor rewrite: old flat name no longer referenced",
          "LER_1-flat.vmdk" not in new_text)
    check("descriptor rewrite: unrelated lines (CID, ddb) untouched",
          'CID=fffffffe' in new_text and 'ddb.virtualHWVersion = "10"' in new_text)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
else:
    print("All logic sanity checks passed.")
