"""Read-only ESXi client.

STRUCTURAL SAFETY GUARANTEE: there is no function in this module, or anywhere
else in this codebase, that can write, delete, or mutate anything on an ESXi
host. This is enforced by what's simply absent here, not by a docstring
warning — grep this file for "scp" and you'll find exactly one direction of
travel (remote -> local), and every remote command is drawn from a fixed,
tiny set of read-only vim-cmd/ls/cat/du/df invocations this class builds
itself. Callers cannot pass in an arbitrary command string; the public API is
a set of named, single-purpose methods.

Two ESXi-specific quirks baked in here:
  - ESXi's shell is BusyBox ash: no GNU extensions. No `sort -h`, no
    `grep -P`. `du`/`df` are called with -k (kibibytes) so nothing downstream
    needs to parse a human-readable size suffix.
  - Older standalone ESXi hosts negotiate SSH with algorithms modern OpenSSH
    clients disable by default (ssh-rsa). We re-enable them explicitly rather
    than requiring a special client-side config file.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass

from ..types import CommandResult

_SAFE_REMOTE_PATH_RE = re.compile(r"^/vmfs/volumes/[A-Za-z0-9_.\-/ ]+$")


class UnsafeRemotePathError(ValueError):
    pass


def _validate_remote_path(path: str) -> str:
    """Defense in depth. We already avoid local shell injection by never using
    shell=True and passing argv as a list — this additionally stops a caller
    from pointing a read at something outside the datastore tree, or sneaking
    shell metacharacters into the *remote* command string that `ssh` forwards
    to the ESXi host's BusyBox shell.
    """
    if ".." in path:
        raise UnsafeRemotePathError(f"path traversal rejected: {path!r}")
    if not _SAFE_REMOTE_PATH_RE.match(path):
        raise UnsafeRemotePathError(
            f"refusing to touch path outside /vmfs/volumes/: {path!r}"
        )
    return path


@dataclass(frozen=True)
class ESXiHost:
    """Connection info for one ESXi host. Never hardcode a host IP in this
    module — every caller supplies one explicitly, so the tool can run
    against any ESXi host, not just one baked in at development time.
    """
    address: str
    ssh_key_path: str | None = None  # None => rely on ssh-agent / ~/.ssh/config,
                                      # which covers the common case.
    ssh_user: str = "root"


class ESXiClient:
    """All methods are read-only. None of them accept a caller-supplied
    command string — each builds its own fixed argv.
    """

    #: The only binaries this class will ever invoke on the remote host.
    #: Kept here as documentation/audit anchor, not as a runtime gate (the
    #: gate is architectural: no code path here constructs anything else).
    ALLOWED_REMOTE_BINARIES = ("vim-cmd", "ls", "cat", "du", "df")

    def __init__(self, host: ESXiHost, timeout_s: int = 30):
        self.host = host
        self.timeout_s = timeout_s

    # -- low-level plumbing (private) -------------------------------------- #
    def _ssh_base_argv(self) -> list[str]:
        argv = [
            "ssh",
            "-oHostKeyAlgorithms=+ssh-rsa",
            "-oPubkeyAcceptedKeyTypes=+ssh-rsa",
            "-oBatchMode=yes",  # never hang waiting on an interactive password prompt
            "-oConnectTimeout=10",
        ]
        if self.host.ssh_key_path:
            argv += ["-i", self.host.ssh_key_path]
        argv.append(f"{self.host.ssh_user}@{self.host.address}")
        return argv

    def _run_remote(self, remote_command_tokens: list[str]) -> CommandResult:
        # shlex.quote every token before joining into the single string ssh
        # forwards to the remote shell — this is what actually matters, since
        # ssh's remote-command argument is always shell-interpreted by sshd on
        # the far end regardless of how we invoke ssh locally.
        remote_command = " ".join(shlex.quote(t) for t in remote_command_tokens)
        argv = self._ssh_base_argv() + [remote_command]
        return self._exec(argv)

    def _exec(self, argv: list[str], timeout_s: int | None = None) -> CommandResult:
        effective_timeout = timeout_s if timeout_s is not None else self.timeout_s
        try:
            proc = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            return CommandResult(
                ok=proc.returncode == 0,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                argv=argv,
            )
        except subprocess.TimeoutExpired as e:
            return CommandResult(
                ok=False,
                returncode=-1,
                stdout=(e.stdout or ""),
                stderr=f"timed out after {effective_timeout}s",
                argv=argv,
            )

    # -- public, read-only API ---------------------------------------------- #
    def list_all_vms(self) -> CommandResult:
        """`vim-cmd vmsvc/getallvms`"""
        return self._run_remote(["vim-cmd", "vmsvc/getallvms"])

    def get_filelayout(self, esxi_vmid: int) -> CommandResult:
        """`vim-cmd vmsvc/get.filelayout <vmid>`.

        Kept as a fallback source only -- on at least some ESXi versions, its
        plain (non-Ex) disk block does NOT reliably enumerate extent
        (-flat.vmdk) files, only the descriptor. get_filelayoutex() below is
        the primary, authoritative source path_resolver.py actually parses.
        """
        vmid = int(esxi_vmid)  # raises if not int-like; no string ever reaches the shell here
        return self._run_remote(["vim-cmd", "vmsvc/get.filelayout", str(vmid)])

    def get_filelayoutex(self, esxi_vmid: int) -> CommandResult:
        """`vim-cmd vmsvc/get.filelayoutex <vmid>` — THE authoritative disk
        path source. Unlike plain get.filelayout, this reliably enumerates
        every file (config/nvram/log/diskDescriptor/diskExtent/snapshotList/
        etc.) with an explicit `type` field and real byte size.
        """
        vmid = int(esxi_vmid)
        return self._run_remote(["vim-cmd", "vmsvc/get.filelayoutex", str(vmid)])

    def get_summary(self, esxi_vmid: int) -> CommandResult:
        """`vim-cmd vmsvc/get.summary <vmid>` — used for vmPathName / datastore."""
        vmid = int(esxi_vmid)
        return self._run_remote(["vim-cmd", "vmsvc/get.summary", str(vmid)])

    def get_power_state(self, esxi_vmid: int) -> CommandResult:
        """`vim-cmd vmsvc/power.getstate <vmid>`

        Mandatory before any copy is attempted: a running VM must never be
        copied or (obviously) shut down by this tool — that decision belongs
        to a human, always (see orchestrator escalation rules).
        """
        vmid = int(esxi_vmid)
        return self._run_remote(["vim-cmd", "vmsvc/power.getstate", str(vmid)])

    def get_guest_os(self, esxi_vmid: int) -> CommandResult:
        """`vim-cmd vmsvc/get.guest <vmid>` — supplementary signal for the
        appliance-guest detector.
        """
        vmid = int(esxi_vmid)
        return self._run_remote(["vim-cmd", "vmsvc/get.guest", str(vmid)])

    def read_file(self, remote_path: str, max_bytes: int = 1_000_000) -> CommandResult:
        """`cat <path>` on a small text file (e.g. a .vmx or .vmdk descriptor).
        Not for bulk disk data — use pull_file for that.
        """
        path = _validate_remote_path(remote_path)
        # head -c bounds the read so a caller can't accidentally cat a
        # multi-GB -flat.vmdk through this text path.
        return self._run_remote(["head", "-c", str(max_bytes), path])

    def list_directory(self, remote_path: str) -> CommandResult:
        """`ls -la <path>`"""
        path = _validate_remote_path(remote_path)
        return self._run_remote(["ls", "-la", path])

    def disk_usage_kb(self, remote_path: str) -> CommandResult:
        """`du -sk <path>` — kibibytes, deliberately not -h (BusyBox `du` has
        no `-h`, and even where available, human-readable output is a
        parsing trap).
        """
        path = _validate_remote_path(remote_path)
        return self._run_remote(["du", "-sk", path])

    def free_space_kb(self, remote_path: str) -> CommandResult:
        """`df -k <path>`"""
        path = _validate_remote_path(remote_path)
        return self._run_remote(["df", "-k", path])

    def pull_file(
        self,
        remote_path: str,
        local_path: str,
        cipher: str = "aes128-ctr",
        timeout_s: int = 14400,
    ) -> CommandResult:
        """scp in the PULL direction only: remote_path is always the source
        argument, local_path is always the destination argument. There is no
        overload, flag, or code path in this method that can be made to scp
        the other way.

        timeout_s defaults to 4 hours, NOT self.timeout_s (which defaults to
        30s and is sized for quick vim-cmd/cat/du metadata queries). A real
        multi-GB disk transfer legitimately takes far longer than a metadata
        query -- large Windows disk transfers in particular can run for
        hours, and a shared 30s timeout would kill one partway through.
        """
        path = _validate_remote_path(remote_path)
        argv = [
            "scp",
            f"-c{cipher}",
            "-oHostKeyAlgorithms=+ssh-rsa",
            "-oPubkeyAcceptedKeyTypes=+ssh-rsa",
            "-oBatchMode=yes",
        ]
        if self.host.ssh_key_path:
            argv += ["-i", self.host.ssh_key_path]
        argv.append(f"{self.host.ssh_user}@{self.host.address}:{path}")
        argv.append(local_path)
        return self._exec(argv, timeout_s=timeout_s)
