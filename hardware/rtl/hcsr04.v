`timescale 1ns / 1ps

module hcsr04 #(
    parameter integer CLK_HZ = 125_000_000,
    parameter integer TRIG_US = 10,
    parameter integer ECHO_TIMEOUT_US = 30_000
)(
    input  wire        clk,
(* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rst RST" *)
(* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME rst, POLARITY ACTIVE_HIGH" *)
    input  wire        rst,
    input  wire        start,
    input  wire        echo_in,
    output reg         trig_out,
    output wire        busy,
    output reg         valid,
    output reg         timeout,
    output reg  [31:0] echo_count
);

    // 时钟频率应为正整数 MHz，例如 100 MHz 或 125 MHz。
    // start 来自与 clk 相同的时钟域，后续会接到 Python 控制接口。
    // 两次测量请求间隔至少 60 ms，由后续 Python 程序保证。
    localparam integer TRIG_CYCLES = (CLK_HZ / 1_000_000) * TRIG_US;
    localparam integer TIMEOUT_CYCLES =
        (CLK_HZ / 1_000_000) * ECHO_TIMEOUT_US;

    localparam [1:0] IDLE = 2'd0;
    localparam [1:0] SEND_TRIG = 2'd1;
    localparam [1:0] WAIT_ECHO = 2'd2;
    localparam [1:0] COUNT_ECHO = 2'd3;

    reg [1:0] state;
    reg [31:0] timer;
    reg [31:0] width_count;
    reg start_d;

    // 两级寄存器同步外部 Echo，降低异步信号引起亚稳态的风险。
    (* ASYNC_REG = "TRUE" *) reg echo_meta;
    (* ASYNC_REG = "TRUE" *) reg echo_sync;

    assign busy = (state != IDLE);

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            echo_meta <= 1'b0;
            echo_sync <= 1'b0;
        end else begin
            echo_meta <= echo_in;
            echo_sync <= echo_meta;
        end
    end

    always @(posedge clk or posedge rst) begin
        if (rst) begin
            state       <= IDLE;
            timer       <= 32'd0;
            width_count <= 32'd0;
            start_d     <= 1'b0;
            trig_out    <= 1'b0;
            valid       <= 1'b0;
            timeout     <= 1'b0;
            echo_count  <= 32'd0;
        end else begin
            start_d <= start;

            case (state)
                IDLE: begin
                    trig_out <= 1'b0;
                    timer <= 32'd0;
                    // 空闲时接收 start 从 0 变成 1 的新请求。
                    if (start && !start_d) begin
                        valid <= 1'b0;
                        timeout <= 1'b0;
                        width_count <= 32'd0;
                        if (echo_sync) begin
                            // Echo 已经是高电平，本次请求报告异常。
                            timeout <= 1'b1;
                        end else begin
                            trig_out <= 1'b1;
                            state <= SEND_TRIG;
                        end
                    end
                end

                SEND_TRIG: begin
                    if (timer == TRIG_CYCLES - 1) begin
                        trig_out <= 1'b0;
                        timer <= 32'd0;
                        state <= WAIT_ECHO;
                    end else begin
                        timer <= timer + 1'b1;
                    end
                end

                WAIT_ECHO: begin
                    if (echo_sync) begin
                        width_count <= 32'd1;
                        state <= COUNT_ECHO;
                    end else if (timer == TIMEOUT_CYCLES - 1) begin
                        timeout <= 1'b1;
                        state <= IDLE;
                    end else begin
                        timer <= timer + 1'b1;
                    end
                end

                COUNT_ECHO: begin
                    if (!echo_sync) begin
                        echo_count <= width_count;
                        valid <= 1'b1;
                        state <= IDLE;
                    end else if (width_count >= TIMEOUT_CYCLES) begin
                        timeout <= 1'b1;
                        state <= IDLE;
                    end else begin
                        width_count <= width_count + 1'b1;
                    end
                end
            endcase
        end
    end

endmodule
