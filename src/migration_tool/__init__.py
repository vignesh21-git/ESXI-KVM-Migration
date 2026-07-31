"""ESXi -> Proxmox migration tool.

Layers, kept structurally separate:
  - clients      subprocess wrappers for ESXi (read-only) and Proxmox.
  - discovery    read-only inventory/path-resolution/collision detection.
  - pipeline     the deterministic functions that actually copy/convert/register.
  - agent        tool schemas + orchestrator loop, whose only means of taking
                 action is calling into discovery/pipeline -- never a raw
                 shell command or SSH invocation of its own.

Runs locally on the Proxmox host. See README.md.
"""
