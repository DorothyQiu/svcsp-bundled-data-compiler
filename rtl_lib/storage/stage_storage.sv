module stage_storage #(
    parameter WIDTH = 1
) (
    input  wire [WIDTH-1:0] D,
    input  wire             click,
    output wire [WIDTH-1:0] Q
);
    genvar bit_index;

    generate
        for (bit_index = 0; bit_index < WIDTH; bit_index = bit_index + 1) begin : g_bit
            click_dff storage_bit (
                .D(D[bit_index]),
                .click(click),
                .Q(Q[bit_index])
            );
        end
    endgenerate
endmodule
