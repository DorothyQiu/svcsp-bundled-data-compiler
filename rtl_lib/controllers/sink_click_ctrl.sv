module sink_click_ctrl (
    input  wire Lreq,
    input  wire reset_n,
    output wire Lack,
    output wire click
);
    wire not_Lack;

    click_dff_reset_n Pi (
        .D(not_Lack),
        .click(click),
        .reset_n(reset_n),
        .Q(Lack)
    );

    not invert_Lack (not_Lack, Lack);
    xor click_logic (click, Lreq, Lack);
endmodule
