# jzsyq

键之寺由崎 · 基于 Zynq 的超声波扫描测距与雷达式可视化

基于 PYNQ-Z2 的软硬件协同原型。FPGA 产生舵机 PWM、发送超声波触发脉冲并计数回波；ARM 端使用 Python 调度扫描和绘图，使用 C 发送循环加速 AXI Quad SPI 对 ST7796 LCD 的数据传输。

这里的“雷达”指超声波扫描测距及半圆图可视化，不是电磁雷达。本系统没有真实角度编码器，也不用于安全关键防撞。

## 工程内容

- `software/`：PYNQ Python 驱动、C 加速源码、稳定演示笔记本及配套 bit/hwh。
- `hardware/rtl/`：`pwm.v`、`hcsr04.v`。
- `hardware/bd/`：原工程 Block Design 和 IP 参数文件。
- `hardware/constraints/`：引脚及异步时序约束。
- `hardware/hdl/`：当前顶层 wrapper。
- `hardware/create_project.tcl`：从相对路径建立 Vivado 项目的辅助脚本。
- `tests/`：桌面模拟测试，不连接硬件；原生 C 自检在板上启用时编译运行。
- `docs/`：接线、实测数据和匿名化运行记录。
- `MANIFEST.sha256`：逐文件校验清单。

## 环境

已有硬件构建使用 Vivado 2026.1，器件为 `xc7z020clg400-1`。板上已确认 Linux `armv7l`、`/usr/bin/gcc` 和 SPI 的连续 `uint32` MMIO 数组。需要 PYNQ、NumPy、Pillow；可选桌面测试依赖另列于 `tests/requirements.txt`。不包含 Vivado/PYNQ 等第三方工具安装包。

## 首次使用

1. 固定好舵机与超声波安装，确认线材无牵拉。按 `docs/WIRING.md` 断电接线；Echo 必须分压，不能把 5 V 直接接到 Pmod。
2. 将 `software/` 内文件上传到板上 `/home/xilinx/jupyter_notebooks/radar/`。保持 bit/hwh 同名配套，C 源码与 Python 加速模块放在一起。
3. 停止其他控制硬件的笔记本。PYNQ 开启、MB102 关闭时，打开 `radar_stable_demo.ipynb`，逐格执行，不要运行全部。
4. 笔记本会先检查文件、加载配置，在舵机未供电时准备中心指令 84，然后初始化 LCD 并启用 C 加速。
5. 仅在已验证的安装与冷启动条件下打开 MB102，肉眼确认实际正前方稳定后，执行扫描单元格。
6. 正常完成后，只有供电/机构/配置均未变化时，才能重复最后一格。退出时先关 MB102，再关 PYNQ。

中心指令 84 仅适用于本次安装的人工校准，不是所有舵机通用的 90 度。指令范围 0～180 已在原实物上测试，其他安装必须另行确认。显示采用两侧归一化的相对方向，未做全角域精密标定。

## 当前扫描参数与实测

路径：84 → 0 → 180 → 0 → 180 → 84；720 次单位移动、241 次测量；每 3 个指令单位采样一次；单位移动等待 0.03 s，测量前末步稳定时间共 0.10 s。目标点按 5 s 历史渐隐，LCD 使用脏矩形更新。

| 指标 | 观测值 | 条件 |
|---|---:|---|
| 初期 Python 驱动整屏发送 | 20.416 s | 480×320 RGB565，单次记录 |
| C 加速整屏发送 | 0.669 s | 同规格完整画面，单次记录 |
| 加速前两轮扫描 | 108.2 s | 241 次测量 |
| 加速后两轮扫描 | 64.4 s | 同一组运动/采样参数，241 次测量 |

整屏发送约加速 30.5 倍；扫描总耗时减少约 40.5%。整屏发送与扫描比较是不同阶段的单次记录，不作为同场景统计均值，也不代表每帧刷新率。未完成系统测距误差曲线、全角域角度误差和长期可靠性测试。

对应运行证据：[初期整屏计时](docs/evidence/full_python.png)、[C 加速整屏计时](docs/evidence/full_native.png)、[加速前扫描](docs/evidence/scan_python.png)、[加速后扫描](docs/evidence/scan_native.png)。截图仅裁去无关界面，不修改测量输出。

C 后端不是 DMA：它通过 ctypes 接收已有 MMIO 指针，16 字节 FIFO 批次发送，按真实发送完成标志等待并排空 RX；Python 每 4096 字节重新获得控制。超时和硬件错误会中止 LCD 传输，不自动重配 FPGA 或发送舵机归位指令。

## FPGA 源码重建

在 Vivado 2026.1 Tcl Console 中，进入本目录后运行：

```tcl
source hardware/create_project.tcl
```

默认只在本目录 `build/` 下创建项目并生成设计输出，不覆盖原工程、不启动板上下载。需要自行执行综合、实现和 Generate Bitstream。辅助脚本是此次整理新增的，尚未重新跑完整综合/实现；已随包附上的是原工程成功构建并经实物运行的 bit/hwh。若 Vivado 提示不同版本 IP 需要升级，先复制一份项目，不要直接覆盖稳定构建。

已有路由报告 WNS +0.305 ns、WHS +0.026 ns，TNS/THS 为 0；仍有外部 I/O 延迟约束方法学警告。不能据此宣称全部外部接口已严格完成时序签核。

## 测试与故障处理

在装有 NumPy、Pillow、pycparser 的 Python 环境中，于仓库根目录运行：

```text
python -B -m unittest discover -s tests -p "test_*.py"
```

桌面测试采用假 MMIO 和虚拟时钟，不会驱动舵机。包含语法/协议模型、路径与帧缓存检查；不等同于物理硬件认证。稳定演示笔记本通过桌面流程测试，但仍须在目标板按顺序完成一次复现。

扫描异常/中断后不会自动回中。停止 Python 不会停止 FPGA PWM；机械异常应立即关闭 MB102。不要向角度寄存器写 0 充当“停止”，也不要直接从未知姿态跳回 84。

## 归属与公开范围

本仓库整理了自定义 RTL、应用层驱动、标准 IP 配置与构建产物。AMD/Xilinx IP、PYNQ 和屏幕厂商资料属于各自权利人，不能将整个项目宣称为从零自研。详见 `THIRD_PARTY_NOTICES.md`。未自动替所有文件附加统一开源许可证；公开发布及授权范围由提交者核对后决定。
