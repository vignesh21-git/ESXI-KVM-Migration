"""ESXi -> Proxmox migration tool.

Two layers, kept structurally separate:
  - tool.*        deterministic functions that actually touch ESXi/Proxmox.
  - orchestrator   the reasoning loop that calls tool.* functions and nothing else.

Runs locally on the Proxmox host. See README.md.
"""
