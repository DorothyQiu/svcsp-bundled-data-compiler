// Half-buffer payload storage selected by the M6 bundled-data architecture.
// Capture is transparent until stage_release closes the latch for output completion.
module bundled_data_storage #(
    parameter integer WIDTH = 1
) (
    input  wire [WIDTH-1:0] data_in,
    output wire [WIDTH-1:0] data_out,
    input  wire             capture,
    input  wire             stage_release
);
    wire latch_enable;

    assign latch_enable = capture && !stage_release;

    transparent_latch #(.WIDTH(WIDTH)) payload_storage (
        .data_in(data_in),
        .en(latch_enable),
        .data_out(data_out)
    );
endmodule
