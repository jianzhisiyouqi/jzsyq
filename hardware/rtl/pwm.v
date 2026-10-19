`timescale 1ns / 1ps

// 固定使用 125 MHz 时钟：20 ms 一帧，脉宽约 0.5~2.5 ms。
// 保留原工程每度 1389 个时钟周期的映射，实际角度需按舵机校准。
module pwm (
    input  wire       clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rst RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME rst, POLARITY ACTIVE_HIGH" *)
    input  wire       rst,
    input  wire [7:0] angle_cmd,
    output reg        pwm_out
);

    localparam [31:0] FRAME_CYCLES = 32'd2_500_000;
    localparam [31:0] MIN_PULSE_CYCLES = 32'd62_500;
    localparam [31:0] CYCLES_PER_DEGREE = 32'd1389;
    localparam [31:0] CENTER_PULSE_CYCLES =
        MIN_PULSE_CYCLES + 32'd90 * CYCLES_PER_DEGREE;

    reg [31:0] frame_count;
    reg [31:0] pulse_cycles;

    // 防止软件误写 181~255，超出范围时限制为 180。
    wire [7:0] safe_angle;
    wire [31:0] requested_pulse_cycles;
    assign safe_angle = (angle_cmd > 8'd180) ? 8'd180 : angle_cmd;
    assign requested_pulse_cycles = MIN_PULSE_CYCLES +
                                    safe_angle * CYCLES_PER_DEGREE;

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            frame_count <= 32'd0;
            pulse_cycles <= CENTER_PULSE_CYCLES;
            pwm_out <= 1'b0;
        end else begin
            if (frame_count == FRAME_CYCLES - 1)
                frame_count <= 32'd0;
            else
                frame_count <= frame_count + 1'b1;

            // 只在新一帧开始时采纳角度，避免帧中修改产生异常脉冲。
            // angle_cmd 必须与 clk 同域，后续由同域 AXI GPIO 驱动。
            if (frame_count == 32'd0) begin
                pulse_cycles <= requested_pulse_cycles;
                pwm_out <= 1'b1;
            end else begin
                pwm_out <= (frame_count < pulse_cycles);
            end
        end
    end

endmodule
