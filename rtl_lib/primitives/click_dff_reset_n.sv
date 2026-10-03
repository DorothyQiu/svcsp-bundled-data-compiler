module click_dff_reset_n (
    input  wire D,
    input  wire click,
    input  wire reset_n,
    output wire Q
);
    reg q;

    always @(posedge click or negedge reset_n) begin
        if (!reset_n)
            q <= 1'b0;
        else
            q <= D;
    end

    assign Q = q;
endmodule
