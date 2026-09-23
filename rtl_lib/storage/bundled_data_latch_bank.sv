// Structural ordinary-BODY bundled-data latch bank: one latch cell per bit.
module bundled_data_latch_bank #(
    parameter integer WIDTH = 1
) (
    input  wire [WIDTH-1:0] data_in,
    input  wire             storage_enable,
    output wire [WIDTH-1:0] data_out
);
    genvar bit_index;
    generate
        for (bit_index = 0; bit_index < WIDTH; bit_index = bit_index + 1) begin : bits
            latch_cell bit_latch(
                .data_in(data_in[bit_index]),
                .enable(storage_enable),
                .data_out(data_out[bit_index])
            );
        end
    endgenerate
endmodule
