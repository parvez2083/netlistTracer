* spice_edge_cases.sp — edge-case SPICE fixtures: long lines, mixed case, tab continuation

* Feature: edge case - very long line handling
.SUBCKT edge_long_line_cell IN OUT
X1 IN OUT A B C D E F G H I J K L M N O P Q R S T U V W X Y Z long_cell_name_that_is_very_long
.ENDS edge_long_line_cell

* Feature: edge case - mixed case in identifiers (SPICE is case-insensitive)
.SUBCKT edge_mixed_case_cell VDD VSS
* Test both uppercase and lowercase variants
X1 A B C mixedCase_cell
X2 a b c MixedCase_Cell
X3 vdd vss gnd MixedCaseGlobal
.ENDS edge_mixed_case_cell

* Feature: edge case - tab character as continuation indent
.SUBCKT edge_tab_continuation_cell IN OUT
* Tab character below (denoted by literal tab before +)
X1 A B
	+ C D E tab_cont_cell
.ENDS edge_tab_continuation_cell
