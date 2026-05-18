// verilog_features.v — consolidated Verilog fixture: concat, generate loops/case/if, param, defparam, gate primitives

// Feature: concatenation with aliases
module concat_alias (
  input wire a,
  input wire b,
  output wire y
);

  wire [1:0] combined;
  assign combined = {a, b};
  assign y = combined[0];

endmodule

// Feature: generate loop
module generate_loop (
  input wire [3:0] in,
  output wire [3:0] out
);

  genvar i;
  generate
    for (i = 0; i < 4; i = i + 1) begin : loop
      assign out[i] = in[i];
    end
  endgenerate

endmodule

// Feature: generate case
module verilog_generate_case #(parameter MODE = 2) (
  input wire [3:0] in,
  output wire [3:0] out
);

  generate
    case (MODE)
      1: begin : mode_1
        assign out = in;
      end
      2: begin : mode_2
        assign out = ~in;
      end
      3: begin : mode_3
        assign out = {in[1:0], in[3:2]};
      end
      default: begin : mode_default
        assign out = 4'b0000;
      end
    endcase
  endgenerate

endmodule

// Feature: generate if
module verilog_generate_if #(parameter WIDTH = 4) (
  input wire [WIDTH-1:0] in,
  output wire [WIDTH-1:0] out
);

  generate
    if (WIDTH > 0) begin : pos_width
      assign out = in;
    end else begin : zero_width
      assign out = {WIDTH{1'b0}};
    end
  endgenerate

endmodule

// Feature: nested generate (2-level for loops)
module nested_generate (
    input [3:0] in0,
    input [3:0] in1,
    output [3:0] out0,
    output [3:0] out1
);

  genvar i, j;

  generate
    for (i = 0; i < 2; i = i + 1) begin : outer
      for (j = 0; j < 2; j = j + 1) begin : inner
        // Use simple direct bit-level assigns (per Blueprint B.5)
        // No arithmetic on indices (i*2+j is out of scope)
        assign out0[i] = in0[i];   // Outer loop variable only
        assign out1[j] = in1[j];   // Inner loop variable only
      end
    end
  endgenerate

endmodule

// Feature: parameterized module specialization
module param_specialize #(
  parameter WIDTH = 8
) (
  input wire [WIDTH-1:0] a,
  input wire [WIDTH-1:0] b,
  output wire [WIDTH-1:0] c
);

  assign c = a & b;

endmodule

module top_param (
  input wire [15:0] x,
  input wire [15:0] y,
  output wire [15:0] z
);

  param_specialize #(.WIDTH(16)) ps (
    .a(x),
    .b(y),
    .c(z)
  );

endmodule

// Feature: defparam parameter override
module verilog_defparam (
  input wire clk, reset,
  output wire [7:0] count
);

  // Instance with default parameter
  counter #(.WIDTH(4)) u_counter (
    .clk(clk),
    .reset(reset),
    .count(count)
  );

  // Override the WIDTH parameter using defparam
  defparam u_counter.WIDTH = 8;

endmodule

module counter #(parameter WIDTH = 4) (
  input wire clk, reset,
  output wire [WIDTH-1:0] count
);

  reg [WIDTH-1:0] count_r;
  assign count = count_r;

  always @(posedge clk or negedge reset)
    if (!reset)
      count_r <= {WIDTH{1'b0}};
    else
      count_r <= count_r + 1'b1;

endmodule

// Feature: built-in gate primitives
module verilog_gate_primitives (
  input wire a, b,
  output wire y_and, y_or, y_nand, y_nor, y_xor, y_xnor,
  output wire y_buf, y_not
);

  and     u_and     (y_and,  a, b);
  or      u_or      (y_or,   a, b);
  nand    u_nand    (y_nand, a, b);
  nor     u_nor     (y_nor,  a, b);
  xor     u_xor     (y_xor,  a, b);
  xnor    u_xnor    (y_xnor, a, b);

  buf     u_buf     (y_buf, a);
  not     u_not     (y_not, a);

endmodule
