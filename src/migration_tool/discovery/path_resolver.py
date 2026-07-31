"""Resolves the authoritative disk path(s) for one ESXi VM.

This is the single most important safety module in the pipeline: a VM's
display name, its containing folder name, and its actual .vmdk filename
routinely do NOT agree with each other on real-world standalone ESXi hosts.
Concrete examples worth guarding against:

  - A VM displayed as "LER_2" can have its real files inside a folder called
    "LER2_New", with the disk itself internally named "LER_1.vmdk".
  - Several unrelated Windows VMs, in different folders, can all name their
    disk "IPv6_WIN10-HOST.vmdk".
  - Multiple VMs in one testbed can all use the same disk filename, e.g.
    "IPv6_RefDUT_2.vmdk" or "Peer_New.vmdk".

Deriving a copy source from the display name or folder name would copy the
wrong VM's disk, or overwrite one VM's staged copy with another's, in every
one of those cases. The only authoritative source is
`vim-cmd vmsvc/get.filelayoutex <vmid>`, which is what this module parses.

NOTE on get.filelayout vs get.filelayoutex: plain `get.filelayout`'s disk
block reliably lists only the descriptor (.vmdk) file on at least some ESXi
versions, NOT the paired `-flat.vmdk` extent. `get.filelayoutex` is what this
module actually parses: it enumerates every file with an explicit `type`
field (`diskDescriptor`, `diskExtent`, `config`, `nvram`, `log`,
`snapshotList`, ...) and a real byte size, which is both more complete and
removes the need for a separate `du` round-trip per disk file to learn its
size.

This output is VMware's semi-structured "VMOMI toString" format, not JSON.
Parsing it is unavoidably a bit of regex archaeology; this module is
deliberately conservative about it -- if the expected structure isn't found,
`ResolvedPath.resolution_ok` comes back False with the raw text attached,
rather than silently returning a guess. Downstream code (copy_engine,
orchestrator) must treat resolution_ok=False as a hard stop.
"""
from __future__ import annotations

import re

from ..clients.esxi_client import ESXiClient
from ..types import ApplianceRisk, DiskFile, PowerState, ResolvedPath

# --------------------------------------------------------------------------- #
# get.filelayoutex parsing
# --------------------------------------------------------------------------- #

_FILEINFO_BLOCK_RE = re.compile(
    r"\(vim\.vm\.FileLayoutEx\.FileInfo\)\s*\{(?P<body>.*?)\}", re.DOTALL
)
_NAME_FIELD_RE = re.compile(r'name\s*=\s*"(?P<name>[^"]*)"')
_TYPE_FIELD_RE = re.compile(r'type\s*=\s*"(?P<type>[^"]*)"')
_SIZE_FIELD_RE = re.compile(r"\bsize\s*=\s*(?P<size>\d+)")
_BRACKETED_PATH_RE = re.compile(r"^\[(?P<datastore>[^\]]+)\]\s*(?P<relpath>.+)$")

_DISK_FILE_TYPES = {"diskDescriptor", "diskExtent"}

_VMPATHNAME_RE = re.compile(r'vmPathName\s*=\s*"\[(?P<datastore>[^\]]+)\]\s*(?P<relpath>[^"]+)"')

_SNAPSHOT_DELTA_RE = re.compile(r"-delta\.vmdk$", re.IGNORECASE)
_SNAPSHOT_NUMBERED_RE = re.compile(r"-\d{6}\.vmdk$", re.IGNORECASE)
_FLAT_RE = re.compile(r"-flat\.vmdk$", re.IGNORECASE)


def _classify_disk_file(name: str) -> str:
    if _SNAPSHOT_DELTA_RE.search(name):
        return "snapshot_delta"
    if _SNAPSHOT_NUMBERED_RE.search(name):
        return "snapshot_descriptor"
    if _FLAT_RE.search(name):
        return "base_flat"
    if name.lower().endswith(".vmdk"):
        return "base_descriptor"
    return "unknown"


def _parse_power_state(power_getstate_raw: str) -> PowerState:
    """The actual output of `vim-cmd vmsvc/power.getstate` is plain text
    ("Retrieved runtime info\\nPowered off\\n"), NOT the structured
    `powerState = "poweredOff"` form some documentation implies. Matching on
    normalized (space-stripped, lowercased) substrings handles both that
    plain-text form and the structured form, in case a different vSphere API
    path returns the latter.
    """
    normalized = power_getstate_raw.lower().replace(" ", "")
    if "poweredon" in normalized:
        return PowerState.ON
    if "poweredoff" in normalized:
        return PowerState.OFF
    if "suspended" in normalized:
        return PowerState.SUSPENDED
    return PowerState.UNKNOWN


def _strip_datastore_bracket(name: str) -> tuple[str | None, str]:
    m = _BRACKETED_PATH_RE.match(name)
    if not m:
        return None, name
    return m.group("datastore"), m.group("relpath")


def _parse_disk_files(filelayoutex_raw: str) -> tuple[list[DiskFile], list[str], str | None]:
    """Returns (disk_files, warnings, datastore_seen_in_this_output)."""
    warnings: list[str] = []
    seen: set[str] = set()
    files: list[DiskFile] = []
    datastore_seen: str | None = None

    blocks = list(_FILEINFO_BLOCK_RE.finditer(filelayoutex_raw))
    if not blocks:
        warnings.append("no FileLayoutEx.FileInfo blocks found -- unexpected output shape")
        return files, warnings, None

    for block in blocks:
        body = block.group("body")
        name_m = _NAME_FIELD_RE.search(body)
        type_m = _TYPE_FIELD_RE.search(body)
        if not name_m or not type_m:
            continue
        if type_m.group("type") not in _DISK_FILE_TYPES:
            continue

        datastore, relpath = _strip_datastore_bracket(name_m.group("name"))
        if datastore:
            datastore_seen = datastore
        if relpath in seen:
            continue
        seen.add(relpath)

        size_m = _SIZE_FIELD_RE.search(body)
        size_bytes = int(size_m.group("size")) if size_m else None

        files.append(DiskFile(relative_path=relpath, role=_classify_disk_file(relpath), size_bytes=size_bytes))

    if not files:
        warnings.append("no diskDescriptor/diskExtent entries found in get.filelayoutex output")

    return files, warnings, datastore_seen


def _has_snapshot_chain(files: list[DiskFile]) -> bool:
    return any(f.role in ("snapshot_delta", "snapshot_descriptor") for f in files)


# --------------------------------------------------------------------------- #
# Appliance-guest detection (known issue: SD-WAN / vendor appliances are not
# supported by disk-conversion -- must be excluded from the plan, not attempted)
# --------------------------------------------------------------------------- #
_APPLIANCE_NAME_HINTS = re.compile(
    r"vmanage|vbond|vsmart|c8000v|csr1000v|viptela|sd-?wan|iosxe|ios-xe|netconf.*controller",
    re.IGNORECASE,
)
_APPLIANCE_GUEST_OS_HINTS = ("otherguest64", "rhel6_64guest", "rhel7_64guest")


def _assess_appliance_risk(
    display_name: str, guest_os_raw: str, annotation: str
) -> tuple[ApplianceRisk, str | None]:
    name_hit = _APPLIANCE_NAME_HINTS.search(display_name) or _APPLIANCE_NAME_HINTS.search(annotation)
    guest_hit = guest_os_raw.strip().lower() in _APPLIANCE_GUEST_OS_HINTS

    if name_hit and guest_hit:
        return (
            ApplianceRisk.LIKELY_APPLIANCE,
            f"name/annotation matches vendor-appliance pattern ({name_hit.group(0)!r}) "
            f"AND guest OS string ({guest_os_raw!r}) is a generic/appliance-typical value -- "
            "this tool's disk-conversion path does not support vendor appliances "
            "(e.g. Cisco SD-WAN/IOS-XE images); needs vendor-specific redeployment instead.",
        )
    if name_hit:
        return (
            ApplianceRisk.LIKELY_APPLIANCE,
            f"name/annotation matches vendor-appliance pattern ({name_hit.group(0)!r}); "
            "excluded from plan by default -- override manually if this is a false positive.",
        )
    return ApplianceRisk.NONE, None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def resolve(
    client: ESXiClient,
    esxi_vmid: int,
    display_name: str = "",
    guest_os_raw: str = "",
    annotation: str = "",
) -> ResolvedPath:
    """The one function allowed to answer "where is this VM's disk". Always
    call this instead of trusting VMSummary fields for a path.
    """
    warnings: list[str] = []

    power_result = client.get_power_state(esxi_vmid)
    power_state = PowerState.UNKNOWN
    if power_result.ok:
        power_state = _parse_power_state(power_result.stdout)
        if power_state == PowerState.UNKNOWN:
            warnings.append(f"could not recognize power state in output: {power_result.stdout!r}")
    else:
        warnings.append(f"power.getstate failed: {power_result.stderr.strip()}")

    layout_result = client.get_filelayoutex(esxi_vmid)
    if not layout_result.ok:
        return ResolvedPath(
            esxi_vmid=esxi_vmid,
            display_name=display_name,
            datastore=None,
            vmx_relative_path=None,
            disk_files=[],
            has_snapshot_chain=False,
            power_state=power_state,
            appliance_risk=ApplianceRisk.NONE,
            appliance_reason=None,
            resolution_ok=False,
            parse_warnings=[f"get.filelayoutex command failed: {layout_result.stderr.strip()}"],
            raw_filelayout=layout_result.stdout,
        )

    disk_files, disk_warnings, datastore_from_layout = _parse_disk_files(layout_result.stdout)
    warnings += disk_warnings

    summary_result = client.get_summary(esxi_vmid)
    datastore = datastore_from_layout
    vmx_relative_path = None
    if summary_result.ok:
        m = _VMPATHNAME_RE.search(summary_result.stdout)
        if m:
            datastore = m.group("datastore")  # summary is authoritative when available
            vmx_relative_path = m.group("relpath")
        else:
            warnings.append("could not find vmPathName in get.summary output; using datastore seen in filelayoutex instead")
    else:
        warnings.append(f"get.summary failed: {summary_result.stderr.strip()}")

    guest_result = client.get_guest_os(esxi_vmid)
    guest_os_for_check = guest_os_raw
    if guest_result.ok and not guest_os_for_check:
        gm = re.search(r'guestId\s*=\s*"([^"]+)"', guest_result.stdout)
        if gm:
            guest_os_for_check = gm.group(1)

    appliance_risk, appliance_reason = _assess_appliance_risk(
        display_name, guest_os_for_check, annotation
    )

    resolution_ok = bool(disk_files) and not any(
        "no FileLayoutEx.FileInfo blocks" in w
        or "no diskDescriptor/diskExtent entries" in w
        or "command failed" in w
        for w in warnings
    )

    return ResolvedPath(
        esxi_vmid=esxi_vmid,
        display_name=display_name,
        datastore=datastore,
        vmx_relative_path=vmx_relative_path,
        disk_files=disk_files,
        has_snapshot_chain=_has_snapshot_chain(disk_files),
        power_state=power_state,
        appliance_risk=appliance_risk,
        appliance_reason=appliance_reason,
        resolution_ok=resolution_ok,
        parse_warnings=warnings,
        raw_filelayout=layout_result.stdout,
    )
