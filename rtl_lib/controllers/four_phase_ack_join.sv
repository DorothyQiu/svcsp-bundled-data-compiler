// Resettable acknowledgement join for an ordinary BODY stage.  M6 bypasses
// this component for M=1.  The M>2 reduction relies on the backend's
// monotonic four-phase assertion and return-to-zero phases.
module four_phase_ack_join #(
    parameter integer M = 2
) (
    input  wire         reset_n,
    input  wire [M-1:0] output_ack,
    output wire         base_Rack
);
    generate
        if (M == 1) begin : direct_connection
            buf direct_acknowledge(base_Rack, output_ack[0]);
        end else begin : reduction_network
            wire [M-2:0] reduction;
            muller_c_element2 first_cell(
                .reset_n(reset_n), .a(output_ack[0]), .b(output_ack[1]), .q(reduction[0])
            );
            genvar output_index;
            for (output_index = 2; output_index < M; output_index = output_index + 1) begin : cells
                muller_c_element2 reduction_cell(
                    .reset_n(reset_n), .a(reduction[output_index - 2]),
                    .b(output_ack[output_index]), .q(reduction[output_index - 1])
                );
            end
            buf joined_acknowledge(base_Rack, reduction[M-2]);
        end
    endgenerate
endmodule
