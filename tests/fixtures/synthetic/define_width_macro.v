`define DATA_W 4

module child_mod (
    input  [`DATA_W-1:0] data_in,
    output [`DATA_W-1:0] data_out
);
endmodule

module top_mod (input [3:0] in_top, output [3:0] out_top);
    child_mod sub_inst (.data_in(in_top), .data_out(out_top));
endmodule
