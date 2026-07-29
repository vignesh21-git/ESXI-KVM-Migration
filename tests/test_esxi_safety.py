"""Verifies the structural safety guarantee of esxi_client.py: no write/delete
capability exists, path validation rejects traversal, and pull_file's scp
argv is always oriented remote->local. Run directly:
`python3 tests/test_esxi_safety.py`
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tool.esxi_client import ESXiClient, ESXiHost, UnsafeRemotePathError, _validate_remote_path

FAILURES = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


# --------------------------------------------------------------------------- #
# No mutating methods exist on the class at all -- this is the actual
# structural guarantee, checked by introspection rather than by trusting a
# comment.
# --------------------------------------------------------------------------- #
public_methods = [m for m in dir(ESXiClient) if not m.startswith("_")]
FORBIDDEN_NAME_FRAGMENTS = ("write", "delete", "rm", "remove", "push", "put", "upload", "exec_arbitrary", "run_command")
offending = [m for m in public_methods if any(frag in m.lower() for frag in FORBIDDEN_NAME_FRAGMENTS)]
check(f"esxi_client: no method names suggest write/delete capability (found methods: {public_methods})",
      offending == [])

check("esxi_client: no generic 'run arbitrary command' method exists",
      "run" not in public_methods and "execute" not in public_methods and "run_command" not in public_methods)

# --------------------------------------------------------------------------- #
# Path validation
# --------------------------------------------------------------------------- #
try:
    _validate_remote_path("/vmfs/volumes/datastore1/../../../etc/passwd")
    check("path traversal rejected", False)
except UnsafeRemotePathError:
    check("path traversal rejected", True)

try:
    _validate_remote_path("/etc/passwd")
    check("path outside /vmfs/volumes rejected", False)
except UnsafeRemotePathError:
    check("path outside /vmfs/volumes rejected", True)

ok_path = _validate_remote_path("/vmfs/volumes/datastore1/VAPT_REF_DUT/IPv6 Ref DUT 20.04.2.vmdk")
check("legitimate datastore path (with space in filename) accepted",
      ok_path == "/vmfs/volumes/datastore1/VAPT_REF_DUT/IPv6 Ref DUT 20.04.2.vmdk")

try:
    _validate_remote_path("/vmfs/volumes/datastore1/foo; rm -rf /")
    check("shell metacharacter in path rejected", False)
except UnsafeRemotePathError:
    check("shell metacharacter in path rejected", True)

# --------------------------------------------------------------------------- #
# pull_file argv orientation: remote source must come before local dest,
# and must be of the form user@host:path (never the reverse)
# --------------------------------------------------------------------------- #
client = ESXiClient(ESXiHost(address="192.168.4.90"))
# We don't actually execute (no network in this test) -- just verify argv
# construction by monkeypatching _exec.
captured = {}
def fake_exec(argv, timeout_s=None):
    captured["argv"] = argv
    captured["timeout_s"] = timeout_s
    from tool.types import CommandResult
    return CommandResult(ok=True, returncode=0, stdout="", stderr="", argv=argv)
client._exec = fake_exec

client.pull_file("/vmfs/volumes/datastore1/foo.vmdk", "/mnt/vm-storage/staging/foo.vmdk")
argv = captured["argv"]
remote_arg = next(a for a in argv if a.startswith("root@"))
local_arg = argv[-1]
check("pull_file: remote arg is the source (contains the datastore path)",
      "datastore1/foo.vmdk" in remote_arg)
check("pull_file: local arg is the destination and matches what was requested",
      local_arg == "/mnt/vm-storage/staging/foo.vmdk")
check("pull_file: remote host in argv matches the configured ESXi host, not something caller-controlled",
      remote_arg.startswith("root@192.168.4.90:"))
check("pull_file: uses a bulk-transfer timeout, not the short metadata-query default "
      "(regression check -- a live 10GB transfer timed out at the old shared 30s default)",
      captured["timeout_s"] is not None and captured["timeout_s"] > 300)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
else:
    print("All ESXi safety checks passed.")
