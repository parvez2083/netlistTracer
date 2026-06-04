module sink_mod (input [5:0] bus);
endmodule

module top_mod_concat (input x, output y);
    // Operands intentionally undeclared — forces _sv_expand_pin_net to fail
    sink_mod sub_inst (.bus({signal_hi, signal_lo}));
endmodule
