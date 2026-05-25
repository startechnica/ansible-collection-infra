#!/usr/bin/env python3
"""Remove all VirtualFloppy devices from a VMware VM.

Why this exists: community.vmware ships modules for cdrom, disk, network,
serial port — but nothing for floppy. VMware VMs ship with a virtual
floppy by default; the kernel polls it forever, generating
"I/O error, dev fd0" on the system console every 10s. Removing the
device entirely is the long-term fix (vs blacklisting the kernel module
in the guest).

Floppy hot-remove is NOT supported by VMware (the API returns
"operation cannot be performed in the current state (Powered on)").
This script skips with an OK exit when the VM is running, so the caller
can run it idempotently — pre-power-on (works) or post-boot (no-op).

Inputs:
  argv:  --vm-name, --datacenter, --folder
  env:   VCENTER_HOSTNAME, VCENTER_USERNAME, VCENTER_PASSWORD,
         VCENTER_VALIDATE_CERTS (default: false)

Output (stdout):
  "OK: no floppy devices on <vm>"           — already clean (no change)
  "OK: removed N floppy device(s) from <vm>" — removed (changed)
  "OK: skipped (powered on) on <vm>"        — VM is running; can't
                                                 hot-remove. Schedule
                                                 a power-cycle to
                                                 actually remove.

Exit codes: 0 on success (changed or unchanged or skipped), non-zero on
real errors.

Caller is expected to set Ansible's `changed_when:` based on whether
"removed" appears in stdout.
"""
from __future__ import annotations

import argparse
import os
import ssl
import sys
import time

from pyVim.connect import Disconnect, SmartConnect
from pyVmomi import vim


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vm-name", required=True)
    ap.add_argument("--datacenter", required=True)
    ap.add_argument("--folder", required=True, help="e.g. /Datacenter/vm/Folder")
    args = ap.parse_args()

    host = os.environ["VCENTER_HOSTNAME"]
    user = os.environ["VCENTER_USERNAME"]
    pwd = os.environ["VCENTER_PASSWORD"]
    validate = os.environ.get("VCENTER_VALIDATE_CERTS", "false").lower() == "true"

    ctx = ssl.create_default_context() if validate else ssl._create_unverified_context()
    si = SmartConnect(host=host, user=user, pwd=pwd, sslContext=ctx)
    try:
        content = si.RetrieveContent()
        # Inventory path: /Datacenter/vm/Folder/VMname
        path = f"{args.folder.rstrip('/')}/{args.vm_name}"
        vm = content.searchIndex.FindByInventoryPath(path)
        if vm is None:
            print(f"FAIL: VM not found at {path}", file=sys.stderr)
            return 2

        floppies = [
            d for d in vm.config.hardware.device
            if isinstance(d, vim.vm.device.VirtualFloppy)
        ]
        if not floppies:
            print(f"OK: no floppy devices on {args.vm_name}")
            return 0

        # Floppy hot-remove isn't supported by VMware on running VMs —
        # the ReconfigVM_Task fails with "operation cannot be performed
        # in the current state (Powered on)". Skip gracefully so the
        # caller (runs once during provisioning, before power-on) stays
        # idempotent on re-runs against already-running VMs.
        if vm.runtime.powerState == vim.VirtualMachinePowerState.poweredOn:
            print(
                f"OK: skipped (powered on) on {args.vm_name} — "
                f"{len(floppies)} floppy device(s) still attached. "
                "Power off and re-run to remove."
            )
            return 0

        device_changes = []
        for fd in floppies:
            change = vim.vm.device.VirtualDeviceSpec()
            change.operation = vim.vm.device.VirtualDeviceSpec.Operation.remove
            change.device = fd
            device_changes.append(change)

        spec = vim.vm.ConfigSpec(deviceChange=device_changes)
        task = vm.ReconfigVM_Task(spec=spec)

        # Poll task synchronously — typically completes in <1s.
        deadline = time.monotonic() + 60
        while task.info.state == vim.TaskInfo.State.running:
            if time.monotonic() > deadline:
                print(f"FAIL: ReconfigVM task timed out on {args.vm_name}", file=sys.stderr)
                return 3
            time.sleep(0.5)

        if task.info.state != vim.TaskInfo.State.success:
            err = task.info.error
            msg = err.msg if err else "unknown error"
            print(f"FAIL: ReconfigVM on {args.vm_name}: {msg}", file=sys.stderr)
            return 4

        print(f"OK: removed {len(floppies)} floppy device(s) from {args.vm_name}")
        return 0
    finally:
        Disconnect(si)


if __name__ == "__main__":
    sys.exit(main())
