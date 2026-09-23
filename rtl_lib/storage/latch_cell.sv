// Structural scalar gated-D latch built from a cross-coupled NAND SR latch.
// It is transparent when enable is high and retains state when enable is low.
module latch_cell (
    input  wire data_in,
    input  wire enable,
    output wire data_out
);
    wire data_in_n;
    wire set_latch_n;
    wire reset_latch_n;
    wire data_out_n;

    not invert_data(data_in_n, data_in);
    nand gate_data_high(set_latch_n, data_in, enable);
    nand gate_data_low(reset_latch_n, data_in_n, enable);
    nand sr_set(data_out, set_latch_n, data_out_n);
    nand sr_reset(data_out_n, reset_latch_n, data_out);
endmodule
