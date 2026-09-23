// Structural launch path from base_raw_Rreq to every BODY output branch.
module four_phase_request_fanout #(
    parameter integer M = 2
) (
    input  wire         base_raw_Rreq,
    output wire [M-1:0] output_raw_Rreq
);
    assign output_raw_Rreq = {M{base_raw_Rreq}};
endmodule
