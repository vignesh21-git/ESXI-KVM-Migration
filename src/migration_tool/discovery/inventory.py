"""Parses `vim-cmd vmsvc/getallvms` into VMSummary records.

IMPORTANT: this output is for human display and VMID discovery ONLY. The File
column here is not a reliable disk path — real ESXi hosts routinely have
display names, folder names, and .vmdk filenames that all disagree with each
other (see path_resolver.py's docstring for concrete examples). Never build a
copy plan from anything parsed here beyond the esxi_vmid.

`getallvms` output has no clean delimiter between the Name and File columns
(File starts with a "[datastore]" bracket, which we anchor on), and no field
is guaranteed free of spaces. This parser is deliberately conservative: if a
line doesn't contain a recognizable "[datastore] ....vmx" marker, it's
reported as a parse_warning on that record rather than silently guessed.
"""
from __future__ import annotations

import re

from ..types import VMSummary

_FILE_MARKER_RE = re.compile(r"\[(?P<datastore>[^\]]+)\]\s*(?P<relpath>\S.*?\.vmx)\s*")
_LINE_START_RE = re.compile(r"^\s*(?P<vmid>\d+)\s+(?P<rest>.+)$")


def parse_getallvms(raw_output: str) -> list[VMSummary]:
    records: list[VMSummary] = []
    lines = raw_output.splitlines()

    for line in lines:
        if not line.strip():
            continue
        if line.strip().lower().startswith("vmid"):
            continue  # header row

        m = _LINE_START_RE.match(line)
        if not m:
            continue  # not a VM row (e.g. a stray banner/log line)

        vmid = int(m.group("vmid"))
        rest = m.group("rest")
        warnings: list[str] = []

        file_m = _FILE_MARKER_RE.search(rest)
        if not file_m:
            # Can't reliably split Name from the rest -- keep the whole
            # remainder as the display name and flag it. Guest OS/version/
            # annotation are left blank rather than guessed.
            records.append(
                VMSummary(
                    esxi_vmid=vmid,
                    display_name=rest.strip(),
                    guest_os_raw="",
                    version="",
                    annotation="",
                    parse_warnings=[
                        "could not locate [datastore] .vmx marker; "
                        "display_name may include the File/GuestOS/Version columns"
                    ],
                )
            )
            continue

        display_name = rest[: file_m.start()].strip()
        tail = rest[file_m.end():].strip()
        tail_parts = tail.split(None, 2)

        guest_os = tail_parts[0] if len(tail_parts) >= 1 else ""
        version = tail_parts[1] if len(tail_parts) >= 2 else ""
        annotation = tail_parts[2] if len(tail_parts) >= 3 else ""

        if not display_name:
            warnings.append("empty display_name after parsing")

        records.append(
            VMSummary(
                esxi_vmid=vmid,
                display_name=display_name,
                guest_os_raw=guest_os,
                version=version,
                annotation=annotation,
                parse_warnings=warnings,
            )
        )

    return records
