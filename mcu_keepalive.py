#!/usr/bin/env python3
"""Inspect the Shimeta MCU; hardware writes require --experimental-run.

Protocol reference: yifengyou/aiot-3588a, ref/smdt_wdt.tar.gz.
This is an independent implementation of the observed bus transactions.
"""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time


DEVICE = Path("/sys/bus/i2c/devices/6-0062")
EXPECTED_COMPATIBLE = b"smdtmcu,STM8S00K3"
EXPECTED_MODEL = b"Shimeta AIoT RK3588A V1.3"
I2C_SLAVE = 0x0703
ADDRESS = 0x62
stop = threading.Event()


def log(event, **fields):
    print(json.dumps({"event": event, "monotonic": time.monotonic(), **fields}),
          flush=True)


def notify_ready():
    """Let systemd finish startup only after the first successful heartbeat."""
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notification:
            notification.sendto(b"READY=1\nSTATUS=MCU handshake complete; heartbeat every 20s", address)


def check_board():
    model = Path("/proc/device-tree/model").read_bytes().rstrip(b"\0")
    compatible = (DEVICE / "of_node/compatible").read_bytes().split(b"\0")
    status = (DEVICE / "of_node/status").read_bytes().rstrip(b"\0")
    if model != EXPECTED_MODEL or EXPECTED_COMPATIBLE not in compatible:
        raise RuntimeError("Board or MCU device tree does not match the reviewed target")
    if status != b"okay" or (DEVICE / "driver").exists():
        raise RuntimeError("MCU unavailable or already controlled by a kernel driver")
    adapter = Path("/sys/class/i2c-adapter/i2c-6/device/of_node").resolve()
    if adapter.name != "i2c@fec80000":
        raise RuntimeError("I2C adapter 6 does not match the reviewed device tree bus")


class MCU:
    def __init__(self, fd):
        self.fd = fd
        self.configuration_may_have_changed = False

    def send(self, data):
        if os.write(self.fd, data) != len(data):
            raise OSError("Short I2C write; stopping without further commands")

    def read_register(self, register):
        # Preserve the reference's separate write/read transactions (with STOP).
        self.send(bytes([register]))
        value = os.read(self.fd, 1)
        if len(value) != 1:
            raise OSError("Short I2C read")
        return value[0]

    def identify(self):
        identity = {"0x3a": self.read_register(0x3A),
                    "0x3b": self.read_register(0x3B)}
        log("identity", registers={k: hex(v) for k, v in identity.items()})
        if identity != {"0x3a": 0x89, "0x3b": 0xB1}:
            raise RuntimeError("MCU values differ from the reviewed reference; refusing commands")

    def prepare(self):
        if stop.is_set():
            return False
        # The reference's lite path enables and acknowledges boot, without disable.
        self.configuration_may_have_changed = True
        self.send(bytes([0x32, 0x01]))
        time.sleep(0.009)
        enabled = self.read_register(0xB2)
        log("enabled_readback", enabled=hex(enabled))
        if enabled != 0x01:
            raise RuntimeError("Unexpected enable readback; MCU state may already have changed")
        self.send(bytes([0x51, 0x33]))
        time.sleep(0.008)
        acknowledged = self.read_register(0xD1)
        log("handshake_readback", acknowledged=hex(acknowledged))
        if acknowledged != 0x33:
            raise RuntimeError("Unexpected handshake readback; MCU state may already have changed")
        return True

    def feed(self):
        self.send(bytes([0x33, 0xAB]))
        log("heartbeat_sent")


@contextmanager
def open_mcu():
    check_board()
    # This lock coordinates our own test and future service. I2C_SLAVE does not force
    # access past a bound kernel driver; existing userspace users must also be checked.
    lock_fd = os.open("/run/lock/rk3588-mcu-keepalive.lock",
                      os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd = os.open("/dev/i2c-6", os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.ioctl(fd, I2C_SLAVE, ADDRESS)
            yield MCU(fd)
        finally:
            os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experimental-run", action="store_true",
                        help="Send the reviewed community handshake and heartbeats")
    args = parser.parse_args(argv)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    mcu = None
    try:
        with open_mcu() as mcu:
            mcu.identify()
            if not args.experimental_run:
                log("inspection_complete", configuration_commands_sent=False)
                return 0
            log("experimental_handshake_start")
            if not mcu.prepare():
                log("stopped_before_configuration")
                return 0
            # Complete the first heartbeat even if a stop request arrived mid-handshake.
            mcu.feed()
            notify_ready()
            while not stop.wait(20):
                mcu.feed()
            # No undocumented attempt to restore state or disable the watchdog.
            log("host_program_stopped", warning="Stopping heartbeats may cause a later reset")
        return 0
    except (OSError, RuntimeError) as error:
        changed = mcu is not None and mcu.configuration_may_have_changed
        log("error", message=str(error), configuration_may_have_changed=changed,
            warning="Stopping heartbeats may cause a later reset" if changed else None)
        return 1


if __name__ == "__main__":
    sys.exit(main())
