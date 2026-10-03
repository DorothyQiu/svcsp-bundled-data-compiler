module source_click_ctrl (
    input  wire Rack,
    input  wire reset_n,
    output wire Rreq,
    output wire click
);
    wire not_Rreq;

    click_dff_reset_n Po (
        .D(not_Rreq),
        .click(click),
        .reset_n(reset_n),
        .Q(Rreq)
    );

    not invert_Rreq (not_Rreq, Rreq);
    xnor click_logic (click, Rreq, Rack);
endmodule
