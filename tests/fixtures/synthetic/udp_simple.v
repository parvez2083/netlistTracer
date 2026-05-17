// Simple Verilog UDP (user-defined primitive) for testing
// Defines a basic XOR primitive and uses it in a module

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

// Another UDP for testing
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
