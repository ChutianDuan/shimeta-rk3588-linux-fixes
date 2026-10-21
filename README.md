# 视美泰 RK3588 Linux 系统移植问题与一键修复

适用于 **视美泰（ShiMeta/SMDT）AIoT-3588A V1.3** 的 Linux 用户空间适配：补齐 MCU 开机初始化与持续心跳，恢复 PCA9555 控制的 USB 端口供电。安装一次后，由 systemd 在每次启动时自动执行，无需登录、保持 SSH 连接或手工发送命令。

这是针对特定板型、设备树和 MCU 的社区修复项目，非厂商官方 BSP；不刷写 MCU 固件，也不替换内核或 DTB。

| 移植后现象 | 已有证据支持的原因 | 本项目处理 |
| --- | --- | --- |
| 启动后约 606～607 秒周期性重启 | 当前移植系统未补齐板载 MCU 开机握手或心跳 | 启用 MCU、完成握手，立即发送心跳，此后每 20 秒一次 |
| USB-A 鼠标不亮、无插入及枚举事件 | PCA9555 缺少驱动及输出初始化，USB 供电使能仍为输入 | 仅将 P0.0/P0.1/P0.7 配置为输出高，保留其他 GPIO |

这些是板级 Linux 移植配套功能缺失的修复，不代表所有 RK3588 板卡都有相同问题。详细分析见 [移植问题说明](docs/porting-issues.md)。

## 一键安装

在**目标板卡 Linux 终端**执行。需要 root/sudo、systemd、Python 3 和 i2c-tools；运行脚本不依赖第三方 Python 包。

Ubuntu / Debian 系统如缺少依赖，可先执行：

```bash
sudo apt-get update
sudo apt-get install -y git python3 i2c-tools
```

下载项目并安装：

```bash
git clone https://github.com/ChutianDuan/shimeta-rk3588-linux-fixes.git
cd shimeta-rk3588-linux-fixes
sudo ./install.sh
```

安装器先检查环境、板型、MCU 节点和身份，再安装脚本与服务，执行配置校验，立即启动两项服务并启用开机自启。成功时，MCU 已完成握手和首个心跳；USB 服务已执行。若 PCA9555 已由内核驱动接管，USB 脚本会跳过直接访问，需要另行确认内核供电配置是否正确。

安装期间不会主动重启板卡。重复安装会重新启动服务、重新握手，但不会同时运行两份本项目的 MCU 进程。USB 重复执行不会切断已经开启的电源。

只做预检：

```bash
sudo ./install.sh --check
```

预检不发送 MCU 启用、握手或心跳配置命令，但身份测读包含 I²C 寄存器选择写入。若正式 MCU 服务已运行，保留其锁并跳过身份测读；检查通过不能替代完整安装与实机验收。若旧的 `rk3588-mcu-keepalive-test.service` 仍在运行，安装器会停止部署并提示先完成单实例交接。

## 开机后自动执行什么

1. `rk3588-mcu-keepalive.service` 校验板型、总线、驱动归属与 MCU 身份。
2. 执行 `0x32=0x01`，等待 9 ms，确认 `0xb2=0x01`。
3. 执行 `0x51=0x33`，等待 8 ms，确认 `0xd1=0x33`。
4. 立即发送 `0x33=0xab`，成功后通知 systemd 启动完成，此后每 20 秒发送一次。
5. 独立的 `shimeta-usb-power.service` 设置 PCA9555 USB 供电位，回读验证并记录状态。

两项服务失败后均由 systemd 每 2 秒重试；错误不会绕过板型或身份校验。MCU 日志中的 `heartbeat_sent` 表示主机侧 I²C 写入成功，心跳没有额外的 MCU 应答检查。启动及重试方式依据 [systemd 服务文档](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)。

## 适用范围

| 项目 | 校验目标 |
| --- | --- |
| 设备树 model | `Shimeta AIoT RK3588A V1.3` |
| 板型 compatible | `rockchip,rk3588-aiot-rk3588a-v1.3` |
| I²C 控制器 | `i2c@fec80000` |
| MCU | `/dev/i2c-6`、地址 `0x62`、compatible 为 `smdtmcu,STM8S00K3` |
| MCU 状态 | `okay`，未绑定内核驱动 |
| 身份寄存器 | `0x3a=0x89`，`0x3b=0xb1` |
| USB GPIO 扩展芯片 | 同控制器下的 `pca9555@20`、`nxp,pca9555` |
| USB 供电引脚 | P0.0/P0.1/P0.7，掩码 `0x83`，有效状态为输出高 |

需要 `/dev/i2c-6` 可用。若缺失，应核对内核 `CONFIG_I2C_CHARDEV`、`i2c-dev` 模块及开机加载配置；本安装器不会自动改写内核或模块配置。总线编号可能随 BSP 改变，因此 MCU 脚本同时检查设备树控制器路径，而不只依赖编号，参见 [Linux I²C 用户空间接口](https://www.kernel.org/doc/html/latest/i2c/dev-interface.html)。

已有厂商 MCU 驱动、第三方喂狗程序或其他写入者时，应先确认归属。项目使用非强制 I²C 访问及进程锁；锁只能协调遵守同一锁约定的程序。其他板型或 MCU 固件不应通过删除校验强行套用。

## 检查与验收

```bash
systemctl is-enabled rk3588-mcu-keepalive.service shimeta-usb-power.service
systemctl is-active rk3588-mcu-keepalive.service shimeta-usb-power.service
sudo journalctl -b -u rk3588-mcu-keepalive.service -n 20 --no-pager -o cat
sudo journalctl -b -u shimeta-usb-power.service --no-pager
sudo cat /var/lib/shimeta-usb-power/last-apply.json
```

MCU 首次日志应依次包含 `identity`、`enabled_readback`、`handshake_readback`、`heartbeat_sent`；之后约每 20 秒继续出现心跳。USB 成功时为 `active/exited`，MCU 为 `active/running`。内核驱动接管时 USB 日志会明确显示跳过，此时可能没有新的状态文件。

持续观察至少 30 分钟，确认 boot ID 不变、uptime 增长、`NRestarts=0`，并检查真实 USB 鼠标枚举与输入。随后在允许中断时安排受控重启，再检查**新启动的日志**和服务状态。`enabled` 只表示配置了自启，不等于重启验收已通过。

历史记录：2026-10-16 原 MCU 方案连续观察 1800.5 秒、60 次检查通过，未再次重启；2026-10-17 USB 两个 USB-A 口均验证鼠标识别和移动输入。**本仓库新增的安装器、启动通知与重试配置尚需目标板实机验证；历史记录未进行主动重启验收。** 详见 [验证记录与边界](docs/validation.md)。

## 失败处理与停止服务

安装器返回非零时，请检查错误输出及本次启动日志。已成功启动的 MCU 服务不会因为后续 USB 安装失败而被安装器主动停止；修复依赖或配置后可重新执行安装。USB 修复不成功时不要反复手工扫描总线或尝试未知寄存器。

MCU 服务退出不会关闭硬件看门狗，**停喂可能再次触发复位**。本项目没有已验证的 MCU 状态回滚命令，不发送 `0x32=0x00`，不提供将“卸载文件”等同于“硬件恢复”的一键卸载。

仅取消 USB 以后开机的自动设置可执行：

```bash
sudo systemctl disable --now shimeta-usb-power.service
```

这不会立即关闭已经开启的 USB 电源。USB 状态记录会在下次执行时更新；若需保留首次状态，应提前备份 `last-apply.json`。

## 开发验证

```bash
python3 -m unittest discover -s tests -v
sh -n install.sh
```

测试用 mock 和临时目录验证协议、通知时机、GPIO 位保护及安装失败路径，不访问硬件。GitHub Actions 还在 Ubuntu 上运行 `systemd-analyze verify`。仓库不收录原始私有日志、个人 SSH 配置、厂商二进制、源码归档或完整固件。

MCU 通信流程参考 [yifengyou/aiot-3588a 的 smdt_wdt 归档](https://github.com/yifengyou/aiot-3588a/blob/06a39afaefbf31567e5c391cc8cadd5370895820/ref/smdt_wdt.tar.gz) 中的协议事实，由 Python 独立实现；USB 位定义来自原厂固件设备树的本地分析。参考资料与根因见 [移植问题说明](docs/porting-issues.md)。
