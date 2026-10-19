"""Manual center calibration for an already powered, stationary servo.

No overlay loading, power switching, LCD writes, automatic recentering, or
persistent configuration changes. Readback is a command, not physical feedback.
"""

import time


def trim_center(overlay, mmio_factory=None, input_fn=input, sleep_fn=time.sleep,
                expected_start=90):
    """Require an explicit known-held start; allow manual unit steps in 80..100.

    Returns a confirmed command on 'save', or None on cancel. Either way the
    servo stays at its last command. Caller must not run another hardware loop
    concurrently. This function cannot detect loss of external servo power.
    """
    if type(expected_start) is not int or not 80 <= expected_start <= 100:
        raise ValueError("expected_start必须是80～100之间已知保持的整数指令。")
    if not overlay.is_loaded():
        raise RuntimeError("当前FPGA配置已经改变，请先确认启动状态。")
    if mmio_factory is None:
        from pynq import MMIO
        mmio_factory = MMIO
    info = overlay.ip_dict["axi_gpio_1"]
    gpio = mmio_factory(info["phys_addr"], info["addr_range"])

    def read_command():
        if not overlay.is_loaded():
            raise RuntimeError("FPGA配置已改变，微调停止，不发送归位指令。")
        return int(gpio.read(0x08)) & 0xFF

    command = read_command()
    if command != expected_start:
        raise RuntimeError(f"当前指令是{command}，不是预期的{expected_start}。"
                           "未移动舵机；请先报告当前状态。")

    print(f"先结束其他扫描程序。MB102必须已开，舵机稳定在指令{expected_start}对应的位置。",
          flush=True)
    print("确认期间没有断电、重新配置FPGA或手动转动舵机，并已标出正前方基准。", flush=True)
    print("若机械异常，立即关闭MB102；停止Python不会关闭FPGA的PWM。", flush=True)
    answer = input_fn("以上条件都满足，输入“开始微调”；其他输入取消：").strip()
    if answer != "开始微调":
        print("已取消，没有发送新指令。", flush=True)
        return None

    print("+：指令加1；-：指令减1；save：确认对齐并记录；q：退出。", flush=True)
    print("范围80～100。指令单位不等于已校准的实际角度。", flush=True)
    try:
        while True:
            if read_command() != command:
                raise RuntimeError("其他程序改变了舵机指令，微调停止。")
            action = input_fn(f"当前指令 {command}，请输入 + / - / save / q：").strip().lower()
            # Recheck after every human input; never act on a stale command.
            if read_command() != command:
                raise RuntimeError("等待输入期间舵机指令改变，微调停止。")
            if action == "save":
                print(f"记录中心指令：{command}。舵机保持此处，没有修改启动默认值或扫描映射。",
                      flush=True)
                return command
            if action == "q":
                print(f"已退出，最后指令{command}，不自动回中。", flush=True)
                return None
            if action not in ("+", "-"):
                print("未移动。只接受 +、-、save 或 q。", flush=True)
                continue
            target = command + (1 if action == "+" else -1)
            if not 80 <= target <= 100:
                print("到达本次微调边界，不再向这个方向移动。", flush=True)
                continue
            gpio.write(0x08, target)
            command = target
            sleep_fn(0.2)
            print(f"已发送指令{command}，请观察是否更接近正前方。", flush=True)
    except BaseException:
        print(f"微调停止，最后软件发送/核对的指令{command}，不自动回中。PWM可能仍在输出。",
              flush=True)
        raise
