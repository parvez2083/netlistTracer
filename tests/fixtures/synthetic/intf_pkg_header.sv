// intf_pkg_header.sv -- regression fixture: SystemVerilog module header with
// package_import_declaration(s) between `module NAME` and the port list `(`.
// Per SV-2017 LRM, this is legal syntax that was being silently dropped.

interface bus_intf;
    logic req;
    logic ack;
    modport responder (input req, output ack);
endinterface

module pkg_import_mod
    import pkg_a::*;
    import pkg_b::TYPE_T;
(
    input wire clk,
    input wire resetn,
    bus_intf.responder bus_if,
    output wire done
);
endmodule
