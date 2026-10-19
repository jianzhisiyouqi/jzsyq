//Copyright 1986-2022 Xilinx, Inc. All Rights Reserved.
//Copyright 2022-2026 Advanced Micro Devices, Inc. All Rights Reserved.
//--------------------------------------------------------------------------------
//Tool Version: Vivado v.2026.1 (win64) Build 6511674 Tue Jun 16 11:02:23 MDT 2026
//Date        : Mon Oct 19 12:59:12 2026
//Host        : jiahao running 64-bit major release  (build 9200)
//Command     : generate_target radar_system_wrapper.bd
//Design      : radar_system_wrapper
//Purpose     : IP block netlist
//--------------------------------------------------------------------------------
`timescale 1 ps / 1 ps

module radar_system_wrapper
   (DDR_addr,
    DDR_ba,
    DDR_cas_n,
    DDR_ck_n,
    DDR_ck_p,
    DDR_cke,
    DDR_cs_n,
    DDR_dm,
    DDR_dq,
    DDR_dqs_n,
    DDR_dqs_p,
    DDR_odt,
    DDR_ras_n,
    DDR_reset_n,
    DDR_we_n,
    FIXED_IO_ddr_vrn,
    FIXED_IO_ddr_vrp,
    FIXED_IO_mio,
    FIXED_IO_ps_clk,
    FIXED_IO_ps_porb,
    FIXED_IO_ps_srstb,
    echo_in,
    lcd_cs,
    lcd_ctrl,
    lcd_mosi,
    lcd_sck,
    oled_iic_scl_io,
    oled_iic_sda_io,
    pwm_out,
    trig_out);
  inout [14:0]DDR_addr;
  inout [2:0]DDR_ba;
  inout DDR_cas_n;
  inout DDR_ck_n;
  inout DDR_ck_p;
  inout DDR_cke;
  inout DDR_cs_n;
  inout [3:0]DDR_dm;
  inout [31:0]DDR_dq;
  inout [3:0]DDR_dqs_n;
  inout [3:0]DDR_dqs_p;
  inout DDR_odt;
  inout DDR_ras_n;
  inout DDR_reset_n;
  inout DDR_we_n;
  inout FIXED_IO_ddr_vrn;
  inout FIXED_IO_ddr_vrp;
  inout [53:0]FIXED_IO_mio;
  inout FIXED_IO_ps_clk;
  inout FIXED_IO_ps_porb;
  inout FIXED_IO_ps_srstb;
  input echo_in;
  output [0:0]lcd_cs;
  output [2:0]lcd_ctrl;
  output lcd_mosi;
  output lcd_sck;
  inout oled_iic_scl_io;
  inout oled_iic_sda_io;
  output pwm_out;
  output trig_out;

  wire [14:0]DDR_addr;
  wire [2:0]DDR_ba;
  wire DDR_cas_n;
  wire DDR_ck_n;
  wire DDR_ck_p;
  wire DDR_cke;
  wire DDR_cs_n;
  wire [3:0]DDR_dm;
  wire [31:0]DDR_dq;
  wire [3:0]DDR_dqs_n;
  wire [3:0]DDR_dqs_p;
  wire DDR_odt;
  wire DDR_ras_n;
  wire DDR_reset_n;
  wire DDR_we_n;
  wire FIXED_IO_ddr_vrn;
  wire FIXED_IO_ddr_vrp;
  wire [53:0]FIXED_IO_mio;
  wire FIXED_IO_ps_clk;
  wire FIXED_IO_ps_porb;
  wire FIXED_IO_ps_srstb;
  wire echo_in;
  wire [0:0]lcd_cs;
  wire [2:0]lcd_ctrl;
  wire lcd_mosi;
  wire lcd_sck;
  wire oled_iic_scl_i;
  wire oled_iic_scl_io;
  wire oled_iic_scl_o;
  wire oled_iic_scl_t;
  wire oled_iic_sda_i;
  wire oled_iic_sda_io;
  wire oled_iic_sda_o;
  wire oled_iic_sda_t;
  wire pwm_out;
  wire trig_out;

  IOBUF oled_iic_scl_iobuf
       (.I(oled_iic_scl_o),
        .IO(oled_iic_scl_io),
        .O(oled_iic_scl_i),
        .T(oled_iic_scl_t));
  IOBUF oled_iic_sda_iobuf
       (.I(oled_iic_sda_o),
        .IO(oled_iic_sda_io),
        .O(oled_iic_sda_i),
        .T(oled_iic_sda_t));
  radar_system radar_system_i
       (.DDR_addr(DDR_addr),
        .DDR_ba(DDR_ba),
        .DDR_cas_n(DDR_cas_n),
        .DDR_ck_n(DDR_ck_n),
        .DDR_ck_p(DDR_ck_p),
        .DDR_cke(DDR_cke),
        .DDR_cs_n(DDR_cs_n),
        .DDR_dm(DDR_dm),
        .DDR_dq(DDR_dq),
        .DDR_dqs_n(DDR_dqs_n),
        .DDR_dqs_p(DDR_dqs_p),
        .DDR_odt(DDR_odt),
        .DDR_ras_n(DDR_ras_n),
        .DDR_reset_n(DDR_reset_n),
        .DDR_we_n(DDR_we_n),
        .FIXED_IO_ddr_vrn(FIXED_IO_ddr_vrn),
        .FIXED_IO_ddr_vrp(FIXED_IO_ddr_vrp),
        .FIXED_IO_mio(FIXED_IO_mio),
        .FIXED_IO_ps_clk(FIXED_IO_ps_clk),
        .FIXED_IO_ps_porb(FIXED_IO_ps_porb),
        .FIXED_IO_ps_srstb(FIXED_IO_ps_srstb),
        .echo_in(echo_in),
        .lcd_cs(lcd_cs),
        .lcd_ctrl(lcd_ctrl),
        .lcd_mosi(lcd_mosi),
        .lcd_sck(lcd_sck),
        .oled_iic_scl_i(oled_iic_scl_i),
        .oled_iic_scl_o(oled_iic_scl_o),
        .oled_iic_scl_t(oled_iic_scl_t),
        .oled_iic_sda_i(oled_iic_sda_i),
        .oled_iic_sda_o(oled_iic_sda_o),
        .oled_iic_sda_t(oled_iic_sda_t),
        .pwm_out(pwm_out),
        .trig_out(trig_out));
endmodule
