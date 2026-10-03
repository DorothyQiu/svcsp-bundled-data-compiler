module click_dff (
    input  wire D,
    input  wire click,
    output wire Q
);
    reg q;

    always @(posedge click) begin
        q <= D;
    end

    assign Q = q;
endmodule
