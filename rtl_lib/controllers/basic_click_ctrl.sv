module basic_click_ctrl (
    input  wire Lreq,
    input  wire Rack,
    input  wire reset_n,
    output wire Lack,
    output wire Rreq,
    output wire click
);
    wire D;
    wire not_Lreq;
    wire not_Lack;
    wire not_Rack;
    wire click_term_0;
    wire click_term_1;

    click_dff_reset_n state_ff (
        .D(D),
        .click(click),
        .reset_n(reset_n),
        .Q(Lack)
    );

    not invert_feedback (D, Lack);
    buf drive_Rreq (Rreq, Lack);

    not invert_Lreq (not_Lreq, Lreq);
    not invert_Lack (not_Lack, Lack);
    not invert_Rack (not_Rack, Rack);
    and click_when_low (click_term_0, not_Lreq, Lack, Rack);
    and click_when_high (click_term_1, Lreq, not_Lack, not_Rack);
    or drive_click (click, click_term_0, click_term_1);
endmodule
