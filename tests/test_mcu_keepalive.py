import unittest
from unittest.mock import Mock, call, patch

from mcu_keepalive import MCU, main, notify_ready, stop


class ProtocolChecks(unittest.TestCase):
    def tearDown(self):
        stop.clear()

    def test_unexpected_identity_sends_no_configuration_commands(self):
        mcu = MCU(5)
        with patch("mcu_keepalive.os.write", return_value=1) as write, \
                patch("mcu_keepalive.os.read", side_effect=[b"\x89", b"\x00"]):
            with self.assertRaises(RuntimeError):
                mcu.identify()
        self.assertEqual(write.call_args_list, [call(5, b"\x3a"), call(5, b"\x3b")])

    def test_short_write_stops_before_the_next_command(self):
        mcu = MCU(5)
        with patch("mcu_keepalive.os.write", return_value=1) as write:
            with self.assertRaises(OSError):
                mcu.prepare()
        self.assertEqual(write.call_args_list, [call(5, b"\x32\x01")])

    def test_handshake_preserves_delays_and_checks_readback(self):
        mcu = MCU(5)
        order = Mock()
        with patch.object(mcu, "send", order.send), \
                patch("mcu_keepalive.time.sleep", order.sleep), \
                patch.object(mcu, "read_register", side_effect=[1, 0x33]) as read:
            mcu.prepare()
        self.assertEqual(order.mock_calls, [
            call.send(b"\x32\x01"), call.sleep(0.009),
            call.send(b"\x51\x33"), call.sleep(0.008),
        ])
        self.assertEqual(read.call_args_list, [call(0xB2), call(0xD1)])

    def test_unexpected_handshake_readback_fails_before_heartbeat(self):
        mcu = MCU(5)
        with patch.object(mcu, "send") as send, \
                patch("mcu_keepalive.time.sleep"), \
                patch.object(mcu, "read_register", side_effect=[1, 0]):
            with self.assertRaises(RuntimeError):
                mcu.prepare()
        self.assertEqual(send.call_args_list, [call(b"\x32\x01"), call(b"\x51\x33")])

    def test_stop_before_handshake_sends_nothing(self):
        mcu = MCU(5)
        stop.set()
        with patch.object(mcu, "send") as send:
            self.assertFalse(mcu.prepare())
        send.assert_not_called()
        self.assertFalse(mcu.configuration_may_have_changed)

    def test_stop_during_handshake_still_sends_the_first_heartbeat(self):
        mcu = MCU(5)
        def interrupted_sleep(_):
            stop.set()
        with patch("mcu_keepalive.open_mcu") as opened, \
                patch("mcu_keepalive.signal.signal"), \
                patch.object(mcu, "identify"), \
                patch.object(mcu, "send") as send, \
                patch.object(mcu, "read_register", side_effect=[1, 0x33]), \
                patch("mcu_keepalive.time.sleep", side_effect=interrupted_sleep):
            opened.return_value.__enter__.return_value = mcu
            self.assertEqual(main(["--experimental-run"]), 0)
        self.assertEqual(send.call_args_list, [
            call(b"\x32\x01"), call(b"\x51\x33"), call(b"\x33\xab"),
        ])

    def test_inspection_never_prepares_or_feeds(self):
        with patch("mcu_keepalive.open_mcu") as opened, \
                patch("mcu_keepalive.signal.signal"):
            mcu = opened.return_value.__enter__.return_value
            self.assertEqual(main([]), 0)
        mcu.identify.assert_called_once()
        mcu.prepare.assert_not_called()
        mcu.feed.assert_not_called()

    def test_systemd_is_not_notified_if_first_heartbeat_fails(self):
        with patch("mcu_keepalive.open_mcu") as opened, \
                patch("mcu_keepalive.signal.signal"), \
                patch("mcu_keepalive.notify_ready") as notify:
            mcu = opened.return_value.__enter__.return_value
            mcu.configuration_may_have_changed = True
            mcu.feed.side_effect = OSError("write failed")
            self.assertEqual(main(["--experimental-run"]), 1)
        notify.assert_not_called()

    def test_ready_follows_first_successful_heartbeat(self):
        order = Mock()
        with patch("mcu_keepalive.open_mcu") as opened, \
                patch("mcu_keepalive.signal.signal"), \
                patch("mcu_keepalive.notify_ready", order.notify):
            mcu = opened.return_value.__enter__.return_value
            mcu.feed.side_effect = lambda: (order.feed(), stop.set())
            self.assertEqual(main(["--experimental-run"]), 0)
        self.assertEqual(order.mock_calls, [call.feed(), call.notify()])

    def test_systemd_abstract_notification_socket(self):
        with patch.dict("os.environ", {"NOTIFY_SOCKET": "@mcu-test"}), \
                patch("mcu_keepalive.socket.socket") as socket:
            notify_ready()
        socket.return_value.__enter__.return_value.sendto.assert_called_once_with(
            b"READY=1\nSTATUS=MCU handshake complete; heartbeat every 20s", "\0mcu-test")


if __name__ == "__main__":
    unittest.main()
