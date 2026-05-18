* Synthetic SPICE flat-deck testbench fixture (flat-deck with top-level instances)
.param vdd=0.85
Xdut_main in1 out1 vdd vss top_block_a
Xtop_block_b  in2 out1 vdd vss top_block_b
Vsupply vdd 0 0.85
.subckt top_block_a a b c d
M1 c a b d nmos W=1u L=1u
.ends top_block_a
.subckt top_block_b a b c d
M1 c a b d nmos W=1u L=1u
.ends top_block_b
.end
