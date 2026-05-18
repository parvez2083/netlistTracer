// sv_interfaces.sv — consolidated SystemVerilog fixture: multiple interface definitions with and without modports

// Feature: simple interface without modports
interface simple_intf;
    logic [7:0] data;
    logic valid;
endinterface

// Feature: parameterized interface
interface param_intf #(parameter WIDTH = 4) (input logic clk);
    logic [WIDTH-1:0] data;
endinterface

// Feature: trivial interface (minimal content)
interface trivial_intf;
    logic a;
    logic b;
endinterface

// Feature: interface with modports (master/slave)
interface modport_intf (input logic clk);
    logic [7:0] data;
    logic valid;
    logic ready;

    modport master (
        output data, valid,
        input ready
    );

    modport slave (
        input data, valid,
        output ready
    );

endinterface
