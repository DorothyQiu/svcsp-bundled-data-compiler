module click_dff (
    input  wire D,
    input  wire click,
    output logic Q
);
    always_ff @(posedge click)
        Q <= D;
endmodule
