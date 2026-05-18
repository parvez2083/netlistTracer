// verilog_udp_genvar.v — consolidated Verilog fixture: UDP definitions + genvar-for with block label

// Feature: Verilog UDP (user-defined primitives) XOR and BUF
primitive udp_xor (y, a, b);
  output y;
  input a, b;

  table
    // a b : y
       0 0 : 0;
       0 1 : 1;
       1 0 : 1;
       1 1 : 0;
  endtable
endprimitive

primitive udp_buf (out, in);
  output out;
  input in;

  table
    // in : out
    0 : 0;
    1 : 1;
    x : x;
  endtable
endprimitive

// Module that uses the UDP
module udp_user (a, b, out1, out2);
  input a, b;
  output out1, out2;

  udp_xor u_xor (.a(a), .b(b), .y(out1));
  udp_buf u_buf (.in(out1), .out(out2));
endmodule

// Top-level module
module top (clk, data_in, data_out);
  input clk, data_in;
  output data_out;

  udp_xor u1 (.a(clk), .b(data_in), .y(data_out));
endmodule

// Feature: generate-for with block label (genvar iteration)
// Expected behavior: instances should include label[i] prefix in flat names
// e.g., gblock[0].ucell, gblock[1].ucell, gblock[2].ucell (NOT three instances all named ucell)
module my_top (
  input clk,
  output out
);
  genvar i;
  generate
    for (i = 0; i < 3; i = i + 1) begin: gblock
      my_cell ucell (.clk(clk), .out(out));
    end
  endgenerate
endmodule

module my_cell (
  input clk,
  output out
);
endmodule
