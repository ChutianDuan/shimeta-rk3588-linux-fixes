import subprocess
import unittest
from unittest.mock import call, patch

import install


class InstallChecks(unittest.TestCase):
    def test_preflight_failure_prevents_any_deployment(self):
        with patch.object(install, "preflight", side_effect=RuntimeError("wrong board")), \
                patch.object(install, "deploy") as deploy:
            self.assertEqual(install.main([]), 1)
        deploy.assert_not_called()

    def test_check_does_not_install(self):
        with patch.object(install, "preflight"), patch.object(install, "deploy") as deploy:
            self.assertEqual(install.main(["--check"]), 0)
        deploy.assert_not_called()

    def test_deployment_verifies_before_starting_mcu_then_usb(self):
        with patch.object(install, "install_file"), \
                patch.object(install, "active", return_value=True), \
                patch.object(install, "run") as run:
            install.deploy()
        self.assertEqual(run.call_args_list, [
            call("systemd-analyze", "verify", *(str(p) for _, p, _ in install.FILES[2:])),
            call("systemctl", "daemon-reload"),
            call("systemctl", "enable", install.MCU),
            call("systemctl", "restart", install.MCU),
            call("systemctl", "enable", install.USB),
            call("systemctl", "restart", install.USB),
        ])

    def test_usb_failure_does_not_stop_working_mcu(self):
        def command(*args):
            if args == ("systemctl", "restart", install.USB):
                raise subprocess.CalledProcessError(1, args, stderr="USB failure")
        with patch.object(install, "preflight"), \
                patch.object(install, "install_file"), \
                patch.object(install, "active", return_value=True), \
                patch.object(install, "run", side_effect=command) as run:
            self.assertEqual(install.main([]), 1)
        self.assertIn(call("systemctl", "restart", install.MCU), run.call_args_list)
        self.assertFalse(any("stop" in c.args or "disable" in c.args for c in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
