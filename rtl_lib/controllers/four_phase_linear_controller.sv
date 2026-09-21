// MVP four-phase bundled-data half-buffer controller.
module four_phase_linear_controller (
    input  wire lreq,
    input  wire rack,
    output wire lack,
    output wire latch_en,
    output wire raw_rreq
);
    reg state;

    // This MVP has no reset port.  Simulation starts in the empty state.
    initial state = 1'b0;

    // Muller C-element behavior for lreq and !rack: set when both are high,
    // clear when both are low, otherwise retain the previous state.
    always @ (lreq or rack) begin
        if (lreq && !rack)
            state <= 1'b1;
        else if (!lreq && rack)
            state <= 1'b0;
    end

    assign lack     = state;
    assign latch_en = state;
    assign raw_rreq = state;
endmodule

// Phase 7A's existing LINEAR template identity bound to the MVP controller.
// The control-only formal names are the already-resolved template contract.
module linear_controller (
    input  wire upstream_req_0,
    output wire upstream_ack_0,
    output wire downstream_req_0,
    input  wire downstream_ack_0,
    output wire local_control,
    input  wire storage_control,
    input  wire delayed_control
);
    wire latch_en;
    wire raw_rreq;

    four_phase_linear_controller controller (
        .lreq(upstream_req_0),
        .rack(downstream_ack_0),
        .lack(upstream_ack_0),
        .latch_en(latch_en),
        .raw_rreq(raw_rreq)
    );

    // local_control is the controller's raw request.  Phase 7A binds it to
    // symbolic_matched_delay when the BODY has a bundled-data path; the delay
    // return is the only downstream-visible request path.  Stages without a
    // matched-delay requirement bind delayed_control directly to local_control.
    assign local_control = raw_rreq;
    assign downstream_req_0 = delayed_control;
endmodule
