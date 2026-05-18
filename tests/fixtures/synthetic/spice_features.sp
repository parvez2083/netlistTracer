* spice_features.sp — consolidated SPICE fixture with multiple feature-specific subckts
* Features: basic, inline comments, controlled sources, coupled inductors, .global directive, continuation-across-comment

.GLOBAL VDD VSS GND

* Feature: basic NAND gate circuit
.SUBCKT nand2 Y A B VDD VSS
M1 Y A VDD VDD pmos W=2u L=1u
M2 Y B VDD VDD pmos W=2u L=1u
M3 Y A 1 VSS nmos W=1u L=1u
M4 1 B VSS VSS nmos W=1u L=1u
.ENDS nand2

.SUBCKT top_spice VDD VSS A B Y
X1 Y A B VDD VSS nand2
.ENDS top_spice

* Feature: inline comments (semicolon and dollar-sign styles)
.SUBCKT spice_inline_comments_cell VDD VSS
* This is a full-line comment
X1 A B C test_cell ; this is an inline comment
X2 D E F test_cell $ another inline style
.ENDS spice_inline_comments_cell

* Feature: controlled sources (B/E/F/G/H elements)
.SUBCKT spice_controlled_sources_cell VDD VSS IN OUT
* Behavioral source (B element)
B1 OUT VSS V=V(IN)*2

* Voltage-controlled voltage source (E element)
E1 N1 VSS IN VSS 1.5

* Voltage-controlled current source (G element)
G1 N2 VSS IN VSS 0.001

* Current-controlled current source (F element, needs V control)
F1 N3 VSS VSRC 1.0

* Current-controlled voltage source (H element, needs V control)
H1 N4 VSS VSRC 1000

* Dummy voltage source for control
VSRC VSS VSS DC 0
.ENDS spice_controlled_sources_cell

* Feature: coupled inductor (K element)
.SUBCKT spice_coupled_inductor_cell VDD VSS
* Define two inductors
L1 A B 1u
L2 C D 1u

* Define coupling between them
K1 L1 L2 0.99

.ENDS spice_coupled_inductor_cell

* Feature: continuation lines across comment lines (+ continuation after * comment)
.SUBCKT spice_continuation_cell VDD VSS
X1 A B
* continuation line below
+ C D E test_cell
.ENDS spice_continuation_cell

.END
