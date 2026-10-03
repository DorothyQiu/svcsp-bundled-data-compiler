module click_dff_reset_n (
    input  wire D,
    input  wire click,
    input  wire reset_n,
    output logic Q
);
    always_ff @(posedge click or negedge reset_n) begin
        if (!reset_n)
            Q <= 1'b0;
        else
            Q <= D;
    end
endmodule
