# 舵机信号：PMODA 物理引脚 1
set_property PACKAGE_PIN Y18 [get_ports pwm_out]
set_property IOSTANDARD LVCMOS33 [get_ports pwm_out]

# 超声波 Trig：PMODA 物理引脚 2
set_property PACKAGE_PIN Y19 [get_ports trig_out]
set_property IOSTANDARD LVCMOS33 [get_ports trig_out]

# 分压后的 Echo：PMODA 物理引脚 3
set_property PACKAGE_PIN Y16 [get_ports echo_in]
set_property IOSTANDARD LVCMOS33 [get_ports echo_in]

# OLED SDA: PMODA pin 7
set_property PACKAGE_PIN U18 [get_ports oled_iic_sda_io]
set_property IOSTANDARD LVCMOS33 [get_ports oled_iic_sda_io]

# OLED SCL: PMODA pin 8
set_property PACKAGE_PIN U19 [get_ports oled_iic_scl_io]
set_property IOSTANDARD LVCMOS33 [get_ports oled_iic_scl_io]

set_property -dict {PACKAGE_PIN W14 IOSTANDARD LVCMOS33} [get_ports {lcd_cs[0]}]
set_property -dict {PACKAGE_PIN Y14 IOSTANDARD LVCMOS33} [get_ports {lcd_ctrl[1]}]
set_property -dict {PACKAGE_PIN T11 IOSTANDARD LVCMOS33} [get_ports {lcd_ctrl[0]}]
set_property -dict {PACKAGE_PIN V16 IOSTANDARD LVCMOS33} [get_ports {lcd_mosi}]
set_property -dict {PACKAGE_PIN W16 IOSTANDARD LVCMOS33} [get_ports {lcd_sck}]
set_property -dict {PACKAGE_PIN V12 IOSTANDARD LVCMOS33} [get_ports {lcd_ctrl[2]}]