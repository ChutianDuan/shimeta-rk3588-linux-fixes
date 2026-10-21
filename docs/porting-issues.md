# 视美泰 RK3588 Linux 系统移植问题说明

本项目针对视美泰 AIoT-3588A V1.3 的两项板级适配缺失。Linux 能启动并不代表原厂 BSP 中的 MCU 通知、看门狗和 USB 供电控制已经完整移植。

## MCU：约十分钟复位

2026-10-16 的原板记录显示，连续 16 个完整启动段末条指标均在启动后约 606～607 秒；温度和内存未发现对应异常。系统没有绑定厂商 MCU 驱动。

同板社区参考代码访问 I²C6 的 `0x62`，设备树和身份测读与原板匹配。补上启用、开机握手和每 20 秒心跳后，原板连续观察 1800.5 秒未再复位。这支持系统移植时遗漏 MCU 初始化或心跳的判断；实验同时加入两者，未单独区分哪一项是必要条件，也未证明 MCU 内部复位逻辑。

本项目保留已实测的独立 I²C 选择寄存器写入和读取事务，中间存在 STOP；没有擅自改成 repeated START。命令失败、短写、身份或握手回读异常会退出，由 systemd 记录并重试。MCU 固件未被修改。

## USB：驱动和板级供电初始化缺失

原板运行内核为 `6.6.89-kdev`，配置显示 `CONFIG_GPIO_PCA953X` 未启用。设备树虽声明 PCA9555，但 `6-0020` 没有绑定驱动。芯片仍能通过用户空间 I²C 访问。

原厂固件导出的设备树中，PCA9555 的 P0.0、P0.1、P0.7 分别用于 `usb0_power`、`usb1_power`、`usb2_power`，默认输出高。故障现场输出锁存寄存器 `0x02=0xff`，方向寄存器 `0x06=0xff`，即全部为输入，预存的高电平没有被主动驱动。

只将 P0.0 设为输出后，原本无插入事件的鼠标立即枚举并产生移动和按键事件；之后启用另两位，第二个 USB-A 口也通过鼠标枚举和移动验证。USB Hub 已枚举和 root hub 存在，均不能单独证明这些独立插口已经供电。

脚本使用掩码 `0x83` 读改写，仅操作第一 bank 的三个供电位：先设置输出锁存，再设置方向，写后回读。发现内核驱动已接管则跳过，不强制访问。操作异常时仅尝试恢复本次保存的 USB 位；恢复本身可能因硬件异常失败，状态文件保留写入前读数以供排查。

## 完整 BSP 适配方向

用户空间服务用于恢复当前系统缺少的功能。后续维护 BSP 时，应补齐厂商 MCU 协议的内核或用户空间管理方案，并在正确的内核及板级设备树中启用 PCA953X 驱动，通过 regulator 或匹配的板级驱动管理 USB 供电。只增加设备树节点或只编入驱动，均不能据此断言端口初始化完成。

原板运行内核与 `/boot` 链接指向的版本不一致，实际加载来源尚未确认。编译或替换内核/DTB 前需先确认引导介质和实际加载文件；本项目不改写启动配置。

## 参考资料

- [同板社区 MCU 参考归档（固定提交）](https://github.com/yifengyou/aiot-3588a/blob/06a39afaefbf31567e5c391cc8cadd5370895820/ref/smdt_wdt.tar.gz)：协议参考，非厂商正式协议；本项目不分发或运行其中的二进制。
- [NXP PCA9555 数据手册](https://www.nxp.com/docs/en/data-sheet/PCA9555.pdf)：寄存器和方向位定义。
- [Linux I²C 用户空间接口](https://www.kernel.org/doc/html/latest/i2c/dev-interface.html)：设备访问与适配器编号。
- [视美泰 AIoT-3588A 板卡介绍](https://shimetaccdevdoc.readthedocs.io/zh-cn/latest/Product-Introduction/ShiMetaCC-AIoT-3588A.html)：板卡背景。
- [systemd 服务配置](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html)：启动通知与失败重启规则。
