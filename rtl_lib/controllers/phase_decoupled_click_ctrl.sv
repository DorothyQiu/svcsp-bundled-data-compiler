module phase_decoupled_click_ctrl (
    input  wire Lreq,
    input  wire Rack,
    input  wire reset_n,
    output wire Lack,
    output wire Rreq,
    output wire click
);
    wire not_Lack;
    wire not_Rreq;
    wire input_pending;
    wire output_ready;

    click_dff_reset_n Pi (
        .D(not_Lack),
        .click(click),
        .reset_n(reset_n),
        .Q(Lack)
    );

    click_dff_reset_n Po (
        .D(not_Rreq),
        .click(click),
        .reset_n(reset_n),
        .Q(Rreq)
    );

    not invert_Lack (not_Lack, Lack);
    not invert_Rreq (not_Rreq, Rreq);
    xor input_pending_logic (input_pending, Lreq, Lack);
    xnor output_ready_logic (output_ready, Rreq, Rack);
    and click_logic (click, input_pending, output_ready);
endmodule
