module matched_delay #(
    parameter NBUF = 1
) (
    input  wire Lreq,
    output wire Lreq_delayed
);
    wire [NBUF:0] delay_node;
    assign delay_node[0] = Lreq;
    assign Lreq_delayed = delay_node[NBUF];

    genvar i;
    generate
        for (i = 0; i < NBUF; i = i + 1) begin : g_buffer
            buf delay_buffer (delay_node[i + 1], delay_node[i]);
        end
    endgenerate
endmodule
