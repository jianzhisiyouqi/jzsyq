# Apply this file during implementation only (USED_IN_SYNTHESIS = false).
# The synchronizer leaf pin is available after linking the OOC checkpoints.
# Echo is asynchronous: exclude only the path into the first synchronizer FF.
# Keep timing analysis between echo_meta and echo_sync enabled.
set_false_path -from [get_ports echo_in] -to [get_pins {radar_system_i/hcsr04_0/inst/echo_meta_reg/D}]

# These pulse outputs have no receiver clock synchronous to clk_fpga_0.
# Pulse widths remain defined by the clocked RTL; verify them on hardware.
set_false_path -to [get_ports pwm_out]
set_false_path -to [get_ports trig_out]
