// Simulation / technology-binding model for a bundled-data control delay.
// A physical implementation must replace this with a characterized matched
// delay element; payload data is deliberately not an RTL port of this module.
module bundled_data_matched_delay #(
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
