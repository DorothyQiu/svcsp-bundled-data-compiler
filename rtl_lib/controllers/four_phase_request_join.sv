// Resettable request join for an ordinary BODY stage.  M6 bypasses this
// component for N=1.  For N>2 the serial C-element reduction is valid under
// the backend's monotonic four-phase assertion and return-to-zero phases.
module four_phase_request_join #(
    parameter integer N = 2
) (
    input  wire         reset_n,
    input  wire [N-1:0] input_req,
    output wire         base_Lreq
);
    generate
        if (N == 1) begin : direct_connection
            buf direct_request(base_Lreq, input_req[0]);
        end else begin : reduction_network
            wire [N-2:0] reduction;
            muller_c_element2 first_cell(
                .reset_n(reset_n), .a(input_req[0]), .b(input_req[1]), .q(reduction[0])
            );
            genvar input_index;
            for (input_index = 2; input_index < N; input_index = input_index + 1) begin : cells
                muller_c_element2 reduction_cell(
                    .reset_n(reset_n), .a(reduction[input_index - 2]),
                    .b(input_req[input_index]), .q(reduction[input_index - 1])
                );
            end
            buf joined_request(base_Lreq, reduction[N-2]);
        end
    endgenerate
endmodule
