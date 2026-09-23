// Structural ordinary-BODY four-phase 1x1 half-buffer controller.
module four_phase_half_buffer_controller (
    input  wire reset_n,
    input  wire base_Lreq,
    input  wire base_Rack,
    output wire base_Lack,
    output wire base_raw_Rreq,
    output wire storage_enable
);
    wire not_base_Rack;
    wire q;

    not invert_downstream_ack(not_base_Rack, base_Rack);
    muller_c_element2 state_element(
        .reset_n(reset_n), .a(base_Lreq), .b(not_base_Rack), .q(q)
    );
    buf return_input_ack(base_Lack, q);
    buf launch_output_request(base_raw_Rreq, q);
    buf enable_output_storage(storage_enable, q);
endmodule
