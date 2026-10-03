module matched_delay #(
    parameter NBUF = 1
) (
    input  wire Lreq,
    output wire Lreq_delayed
);
    generate
        if (NBUF == 0) begin : g_no_delay
            buf bypass (Lreq_delayed, Lreq);
        end
        else begin : g_delay
            wire [NBUF-1:0] delay_node;
            genvar buffer_index;

            for (buffer_index = 0; buffer_index < NBUF;
                 buffer_index = buffer_index + 1) begin : g_buffer
                if (buffer_index == 0) begin : g_first
                    buf delay_buffer (delay_node[buffer_index], Lreq);
                end
                else begin : g_later
                    buf delay_buffer (
                        delay_node[buffer_index],
                        delay_node[buffer_index - 1]
                    );
                end
            end

            buf output_buffer (Lreq_delayed, delay_node[NBUF - 1]);
        end
    endgenerate
endmodule
