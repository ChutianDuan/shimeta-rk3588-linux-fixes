from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import shimeta_usb_power as usb


class USBPowerChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.node = self.root / "sys/firmware/devicetree/base/i2c@fec80000/pca9555@20"
        self.node.mkdir(parents=True)
        (self.node / "compatible").write_bytes(b"nxp,pca9555\0")
        board = self.root / "proc/device-tree/compatible"
        board.parent.mkdir(parents=True)
        board.write_bytes(usb.BOARD + b"\0")
        self.device = self.root / "sys/bus/i2c/devices/6-0020"
        self.device.mkdir(parents=True)
        (self.device / "of_node").symlink_to(self.node)
        (self.root / "dev").mkdir()
        (self.root / "dev/i2c-6").touch()
        (self.root / "run/lock").mkdir(parents=True)
        self.registers = {2: 0x14, 3: 0x5A, 6: 0xFF, 7: 0xA5}
        self.writes = []
        self.fail_direction_once = False

    def command(self, args, **kwargs):
        self.assertNotIn("-f", args)
        register = int(args[4], 16)
        if args[0] == "/usr/sbin/i2cget":
            return subprocess.CompletedProcess(args, 0, stdout=hex(self.registers[register]))
        value = int(args[5], 16)
        self.writes.append((register, value))
        if register == 6 and self.fail_direction_once:
            self.fail_direction_once = False
            raise subprocess.CalledProcessError(1, args)
        self.registers[register] = value
        return subprocess.CompletedProcess(args, 0)

    def apply(self):
        def mapped_path(value):
            return self.root / str(value).lstrip("/")
        with patch.object(usb, "Path", side_effect=mapped_path), \
                patch.object(usb, "NODE", self.node), \
                patch.object(usb, "STATE", self.root / "state"), \
                patch.object(usb.os, "geteuid", return_value=0), \
                patch.object(usb.subprocess, "run", side_effect=self.command):
            usb.main()

    def test_only_usb_bits_change_and_latch_precedes_direction(self):
        self.apply()
        self.assertEqual(self.writes, [(2, 0x97), (6, 0x7C)])
        self.assertEqual(self.registers, {2: 0x97, 3: 0x5A, 6: 0x7C, 7: 0xA5})

    def test_repeated_apply_does_not_toggle_power(self):
        self.apply()
        self.writes.clear()
        self.apply()
        self.assertEqual(self.writes, [])

    def test_bound_kernel_driver_prevents_bus_access(self):
        (self.device / "driver").touch()
        self.apply()
        self.assertEqual(self.writes, [])
        self.assertFalse((self.root / "state").exists())

    def test_wrong_board_prevents_bus_access(self):
        (self.root / "proc/device-tree/compatible").write_bytes(b"other-board\0")
        with self.assertRaises(RuntimeError):
            self.apply()
        self.assertEqual(self.writes, [])

    def test_partial_failure_restores_only_the_usb_bits(self):
        before = self.registers.copy()
        self.fail_direction_once = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.apply()
        self.assertEqual(self.registers, before)


if __name__ == "__main__":
    unittest.main()
