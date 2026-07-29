"""Standalone sanity checks for the text parsers, using realistic sample
output shaped like what real standalone ESXi hosts produced during the
manual migration project (including the exact kind of name/filename
mismatches and embedded spaces that made this parsing worth being careful
about). Run directly: `python3 tests/test_parsers.py`
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tool.inventory import parse_getallvms
from tool.path_resolver import _parse_disk_files, _assess_appliance_risk, _classify_disk_file

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


# --------------------------------------------------------------------------- #
# getallvms: name containing spaces, several VMs, one intentionally malformed
# --------------------------------------------------------------------------- #
GETALLVMS_SAMPLE = """Vmid       Name                                    File                                      Guest OS          Version   Annotation
36         VAPT_DUT-SCTP (REF-R2-18)               [datastore1] VAPT_REF_DUT/IPv6 Ref DUT 20.04.2.vmx   ubuntu64Guest     vmx-11
38         Linux_Client                            [datastore1] Linux_Client/Linux_Client.vmx           ubuntu64Guest     vmx-13
135        LER_2                                   [datastore1] LER2_New/LER_1.vmx                      otherLinux64Guest vmx-10    internal name mismatch, real disk is LER_1.vmdk
"""

parsed = parse_getallvms(GETALLVMS_SAMPLE)
check("getallvms: parses 3 VM rows", len(parsed) == 3)
check("getallvms: vmid 36 name has embedded space+parens preserved",
      parsed[0].display_name == "VAPT_DUT-SCTP (REF-R2-18)")
check("getallvms: vmid 135 (LER_2) display name doesn't leak the .vmx path",
      parsed[2].display_name == "LER_2" and "LER2_New" not in parsed[2].display_name)
check("getallvms: annotation captured for row with trailing text",
      "internal name mismatch" in parsed[2].annotation)

# --------------------------------------------------------------------------- #
# get.filelayoutex: base disk only. Shaped exactly like real output captured
# live from a standalone ESXi host during this tool's own build (see
# README's Phase 6 notes) -- an earlier version of this fixture assumed the
# plain (non-Ex) get.filelayout shape, which turned out NOT to reliably
# enumerate -flat.vmdk extents at all on that host. filelayoutex does, with
# explicit `type` fields, which is what's actually parsed now.
# --------------------------------------------------------------------------- #
FILELAYOUTEX_SIMPLE = """(vim.vm.FileLayoutEx) {
   file = (vim.vm.FileLayoutEx.FileInfo) [
      (vim.vm.FileLayoutEx.FileInfo) {
         key = 0,
         name = "[datastore1] VAPT_REF_DUT/IPv6 Ref DUT 20.04.2.vmx",
         type = "config",
         size = 3285,
         uniqueSize = 3285,
         accessible = true
      },
      (vim.vm.FileLayoutEx.FileInfo) {
         key = 1,
         name = "[datastore1] VAPT_REF_DUT/IPv6 Ref DUT 20.04.2.vmdk",
         type = "diskDescriptor",
         size = 0,
         uniqueSize = 0,
         accessible = true
      },
      (vim.vm.FileLayoutEx.FileInfo) {
         key = 2,
         name = "[datastore1] VAPT_REF_DUT/IPv6 Ref DUT 20.04.2-flat.vmdk",
         type = "diskExtent",
         size = 17179869184,
         uniqueSize = 17179869184,
         accessible = true
      },
      (vim.vm.FileLayoutEx.FileInfo) {
         key = 3,
         name = "[datastore1] VAPT_REF_DUT/vmware.log",
         type = "log",
         size = 12345,
         uniqueSize = 12345,
         accessible = true
      }
   ],
}
"""

files, warnings, datastore = _parse_disk_files(FILELAYOUTEX_SIMPLE)
check("filelayoutex(simple): finds exactly 2 disk files", len(files) == 2)
check("filelayoutex(simple): no warnings", warnings == [])
check("filelayoutex(simple): datastore extracted from bracketed name", datastore == "datastore1")
roles = {f.role for f in files}
check("filelayoutex(simple): classifies base_descriptor + base_flat",
      roles == {"base_descriptor", "base_flat"})
check("filelayoutex(simple): does not pick up the .vmx/.log files as disks",
      all(f.relative_path.endswith(".vmdk") for f in files))
check("filelayoutex(simple): flat file's real size captured",
      any(f.role == "base_flat" and f.size_bytes == 17179869184 for f in files))
check("filelayoutex(simple): relative_path has the [datastore1] bracket stripped",
      all(not f.relative_path.startswith("[") for f in files))

# --------------------------------------------------------------------------- #
# get.filelayoutex: snapshot chain present
# --------------------------------------------------------------------------- #
FILELAYOUTEX_SNAPSHOT = """(vim.vm.FileLayoutEx) {
   file = (vim.vm.FileLayoutEx.FileInfo) [
      (vim.vm.FileLayoutEx.FileInfo) {
         key = 0,
         name = "[datastore1] Peer_Pfsense/Pfsense.vmx",
         type = "config",
         size = 2000,
         accessible = true
      },
      (vim.vm.FileLayoutEx.FileInfo) {
         key = 1,
         name = "[datastore1] Peer_Pfsense/Pfsense.vmdk",
         type = "diskDescriptor",
         size = 0,
         accessible = true
      },
      (vim.vm.FileLayoutEx.FileInfo) {
         key = 2,
         name = "[datastore1] Peer_Pfsense/Pfsense-flat.vmdk",
         type = "diskExtent",
         size = 8589934592,
         accessible = true
      },
      (vim.vm.FileLayoutEx.FileInfo) {
         key = 3,
         name = "[datastore1] Peer_Pfsense/Pfsense-000001.vmdk",
         type = "diskDescriptor",
         size = 0,
         accessible = true
      },
      (vim.vm.FileLayoutEx.FileInfo) {
         key = 4,
         name = "[datastore1] Peer_Pfsense/Pfsense-000001-delta.vmdk",
         type = "diskExtent",
         size = 104857600,
         accessible = true
      }
   ],
}
"""

files2, warnings2, datastore2 = _parse_disk_files(FILELAYOUTEX_SNAPSHOT)
check("filelayoutex(snapshot): finds 4 disk files", len(files2) == 4)
roles2 = [f.role for f in files2]
check("filelayoutex(snapshot): classifies all four roles correctly",
      roles2 == ["base_descriptor", "base_flat", "snapshot_descriptor", "snapshot_delta"])

from tool.types import DiskFile
has_chain = any(f.role in ("snapshot_delta", "snapshot_descriptor") for f in files2)
check("filelayout(snapshot): has_snapshot_chain would be True", has_chain)

# --------------------------------------------------------------------------- #
# appliance detection
# --------------------------------------------------------------------------- #
risk1, reason1 = _assess_appliance_risk("vManage-Controller", "otherguest64", "")
check("appliance: vManage flagged as likely appliance", risk1.value == "likely_appliance")

risk2, reason2 = _assess_appliance_risk("Debian-BGPv6", "debian12_64Guest", "")
check("appliance: plain Debian VM NOT flagged", risk2.value == "none")

risk3, reason3 = _assess_appliance_risk("NetconfServer", "ubuntu64Guest", "")
check("appliance: generic 'NetconfServer' name alone is NOT flagged (no vendor keyword match)",
      risk3.value == "none")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
else:
    print("All parser sanity checks passed.")
