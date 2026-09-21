// Simulation-oriented bundled-data matched delay.  This is not a
// technology-mapped, synthesizable physical delay-line implementation.
`timescale 1ns/1ps
module matched_delay #(
    parameter integer DELAY = 1
) (
    input  wire in,
    output wire out
);
    assign #(DELAY) out = in;
endmodule

// Phase 7A's symbolic matched-delay template identity bound to the MVP model.
module symbolic_matched_delay #(
    parameter integer DELAY = 1
) (
    input  wire control_in,
    output wire control_out
);
    matched_delay #(.DELAY(DELAY)) delay_element (
        .in(control_in),
        .out(control_out)
    );
endmodule
