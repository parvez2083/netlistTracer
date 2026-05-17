// Test case: generate-for with block label (genvar iteration)
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
