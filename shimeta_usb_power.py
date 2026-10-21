#!/usr/bin/python3
"""Enable the three documented USB power switches on Shimeta AIoT-3588A V1.3."""

import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time


MASK = 0x83  # PCA9555 P0.0, P0.1, P0.7; vendor smdtio defaults are high.
BOARD = b"rockchip,rk3588-aiot-rk3588a-v1.3"
NODE = Path("/sys/firmware/devicetree/base/i2c@fec80000/pca9555@20")
STATE = Path("/var/lib/shimeta-usb-power")


def main():
    if os.geteuid() != 0:
        raise RuntimeError("Root is required to control the USB power switches")
    compatible = Path("/proc/device-tree/compatible").read_bytes().split(b"\0")
    if BOARD not in compatible:
        raise RuntimeError("Unsupported board; no hardware changes made")
    with Path("/run/lock/shimeta-usb-power.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        device = None
        for _ in range(50):
            for candidate in Path("/sys/bus/i2c/devices").glob("*-0020"):
                if (candidate / "of_node").resolve() == NODE:
                    bus = candidate.name.split("-")[0]
                    if Path("/dev/i2c-" + bus).exists():
                        device = candidate
                        break
            if device is not None:
                break
            time.sleep(0.2)
        if device is None:
            raise RuntimeError("The board's PCA9555 I2C device is unavailable")
        if b"nxp,pca9555" not in (NODE / "compatible").read_bytes().split(b"\0"):
            raise RuntimeError("Unexpected GPIO expander; no hardware changes made")

        def driver_bound():
            return (device / "driver").exists()

        if driver_bound():
            print("PCA9555 is owned by a kernel driver; skipping userspace control")
            return

        def read(reg):
            if driver_bound():
                raise RuntimeError("PCA9555 acquired by a kernel driver")
            result = subprocess.run(
                ["/usr/sbin/i2cget", "-y", bus, "0x20", hex(reg), "b"],
                check=True, capture_output=True, text=True, timeout=3,
            )
            return int(result.stdout.strip(), 16)

        def write(reg, value):
            if driver_bound():
                raise RuntimeError("PCA9555 acquired by a kernel driver")
            subprocess.run(
                ["/usr/sbin/i2cset", "-y", bus, "0x20", hex(reg), hex(value), "b"],
                check=True, capture_output=True, text=True, timeout=3,
            )
            if read(reg) != value:
                raise RuntimeError("PCA9555 register readback mismatch")

        before = {str(reg): read(reg) for reg in (2, 3, 6, 7)}
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = {
            "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "device": device.name, "mask": MASK, "before": before,
        }
        # Save recovery data before the first hardware write.
        (STATE / "last-apply.json").write_text(json.dumps(record, indent=2) + "\n")
        try:
            # Set the output latch first, then enable only the USB output pins.
            for reg, value in ((2, before["2"] | MASK), (6, before["6"] & ~MASK)):
                if read(reg) != value:
                    write(reg, value)
            after = {str(reg): read(reg) for reg in (2, 3, 6, 7)}
            if (after["2"] & MASK) != MASK or (after["6"] & MASK) != 0:
                raise RuntimeError("USB power output state verification failed")
            if any((before[str(reg)] ^ after[str(reg)]) & ~MASK for reg in (2, 6)):
                raise RuntimeError("An unrelated GPIO bit changed")
            if any(before[str(reg)] != after[str(reg)] for reg in (3, 7)):
                raise RuntimeError("An unrelated GPIO bank changed")
        except Exception:
            if not driver_bound():
                for reg in (6, 2):
                    write(reg, (read(reg) & ~MASK) | (before[str(reg)] & MASK))
            raise
        record["after"] = after
        (STATE / "last-apply.json").write_text(json.dumps(record, indent=2) + "\n")
        print(f"USB power enabled: {device.name}, P0.0/P0.1/P0.7 high; other GPIO bits preserved")


if __name__ == "__main__":
    main()
