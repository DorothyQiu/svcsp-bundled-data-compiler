module stage_storage #(
    parameter WIDTH = 1
) (
    input  wire [WIDTH-1:0] D,
    input  wire             click,
    output wire [WIDTH-1:0] Q
);
    genvar i;

    generate
        for (i = 0; i < WIDTH; i = i + 1) begin : g_bit
            click_dff ff (
                .D(D[i]),
                .click(click),
                .Q(Q[i])
            );
        end
    endgenerate
endmodule
