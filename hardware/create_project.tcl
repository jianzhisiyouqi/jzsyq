# Portable source reconstruction helper; not a new tested bitstream build.
set project_root [file normalize [file join [file dirname [info script]] ..]]
set build_root [file join $project_root build]
set project_dir [file join $build_root radar]
if {[file exists $project_dir]} {
    error "Build target already exists. Choose a new copy; this helper never overwrites it: $project_dir"
}
file mkdir $build_root
create_project radar $project_dir -part xc7z020clg400-1
set_property target_language Verilog [current_project]
add_files -norecurse [list [file join $project_root hardware rtl pwm.v] [file join $project_root hardware rtl hcsr04.v]]
update_compile_order -fileset sources_1
import_files -norecurse [file join $project_root hardware bd radar_system radar_system.bd]
add_files -fileset constrs_1 -norecurse [file join $project_root hardware constraints pynq_z2.xdc]
add_files -fileset constrs_1 -norecurse [file join $project_root hardware constraints radar_async.xdc]
set async_file [get_files radar_async.xdc]
set_property USED_IN_SYNTHESIS false $async_file
set_property USED_IN_IMPLEMENTATION true $async_file
set bd_file [get_files radar_system.bd]
open_bd_design $bd_file
validate_bd_design
save_bd_design
generate_target all $bd_file
set wrappers [make_wrapper -files $bd_file -top]
add_files -norecurse $wrappers
set_property top radar_system_wrapper [get_filesets sources_1]
update_compile_order -fileset sources_1
puts "Project prepared. Review design, then launch synthesis/implementation manually. No board download performed."
