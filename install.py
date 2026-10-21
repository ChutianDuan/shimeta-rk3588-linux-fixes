#!/usr/bin/env python3
"""Install the reviewed userspace fixes on Shimeta AIoT-3588A V1.3."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import mcu_keepalive
from shimeta_usb_power import BOARD, NODE


ROOT = Path(__file__).resolve().parent
MCU = "rk3588-mcu-keepalive.service"
USB = "shimeta-usb-power.service"
FILES = (
    ("mcu_keepalive.py", Path("/usr/local/sbin/rk3588-mcu-keepalive.py"), 0o700),
    ("shimeta_usb_power.py", Path("/usr/local/sbin/shimeta-usb-power"), 0o700),
    ("systemd/" + MCU, Path("/etc/systemd/system") / MCU, 0o644),
    ("systemd/" + USB, Path("/etc/systemd/system") / USB, 0o644),
)


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True)


def active(unit):
    return subprocess.run(("systemctl", "is-active", "--quiet", unit),
                          check=False).returncode == 0


def preflight():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("请在目标 Linux 板卡上使用 sudo ./install.sh 执行")
    for command in ("systemctl", "systemd-analyze", "journalctl"):
        if shutil.which(command) is None:
            raise RuntimeError("缺少命令：" + command)
    for command in ("/usr/bin/python3", "/usr/sbin/i2cget", "/usr/sbin/i2cset"):
        if not os.access(command, os.X_OK):
            raise RuntimeError("缺少依赖：" + command + "；请先安装 python3、i2c-tools")
    run("systemctl", "show", "--property=Version")
    mcu_keepalive.check_board()
    compatible = Path("/proc/device-tree/compatible").read_bytes().split(b"\0")
    if BOARD not in compatible or b"nxp,pca9555" not in (NODE / "compatible").read_bytes().split(b"\0"):
        raise RuntimeError("板型或 PCA9555 节点与已验证目标不符")
    if not Path("/dev/i2c-6").exists():
        raise RuntimeError("缺少 /dev/i2c-6；请确认 i2c-dev 已加载并配置开机加载")
    if active("rk3588-mcu-keepalive-test.service"):
        raise RuntimeError("临时 MCU 服务仍在运行；请按原部署流程完成单实例交接")
    # An existing official service owns the lock and continues feeding while files
    # are prepared. On first install, identify the MCU before changing any files.
    if not active(MCU):
        with mcu_keepalive.open_mcu() as mcu:
            mcu.identify()
    for source, _, _ in FILES:
        if not (ROOT / source).is_file():
            raise RuntimeError("项目文件缺失：" + source)


def install_file(source, target, mode):
    data = source.read_bytes()
    if target.is_file() and target.read_bytes() == data:
        os.chown(target, 0, 0)
        target.chmod(mode)
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + target.name + ".", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.fchown(output.fileno(), 0, 0)
            os.fchmod(output.fileno(), mode)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return True


def deploy():
    # Atomic replacement leaves a running Python process feeding the MCU while
    # deployment is prepared. Never stop a working heartbeat on a later USB error.
    for source, target, mode in FILES:
        install_file(ROOT / source, target, mode)
    run("systemd-analyze", "verify", *(str(target) for _, target, _ in FILES[2:]))
    run("systemctl", "daemon-reload")
    for unit in (MCU, USB):
        run("systemctl", "enable", unit)
        # Restart also applies files left by an interrupted earlier install.
        run("systemctl", "restart", unit)
        if not active(unit):
            raise RuntimeError(unit + " 未处于 active 状态，请查看本次启动日志")
    print("安装成功：MCU 握手和首个心跳已完成，USB 服务已执行，两项服务均已启用开机自启。")
    print("后续启动无需手工执行命令。请按 README 完成持续观察和受控重启验收。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="只检查环境和 MCU 身份，不安装或发送配置命令")
    args = parser.parse_args(argv)
    try:
        preflight()
        if args.check:
            print("预检通过；MCU 服务运行时保留其 I²C 锁，不重复测读身份。")
            return 0
        deploy()
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print("安装未完成：" + str(error), file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr or error.stdout or "", file=sys.stderr)
        print("检查 journalctl -b -u " + MCU + " -u " + USB +
              "；已运行的 MCU 心跳不会因安装失败而被主动停止。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
