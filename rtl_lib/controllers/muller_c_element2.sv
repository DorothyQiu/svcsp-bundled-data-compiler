// Structural resettable two-input Muller C-element built from a canonical
// cross-coupled NAND SR latch.  reset_n forces q low.
module muller_c_element2 (
    input  wire reset_n,
    input  wire a,
    input  wire b,
    output wire q
);
    wire set_latch_n;
    wire reset_latch_n;
    wire any_high;
    wire q_bar;

    // The active-low SR inputs encode the C-element relation.  reset_latch_n
    // is low for reset or all-low inputs, so reset dominates a simultaneous
    // set condition.
    nand make_set_latch_n(set_latch_n, reset_n, a, b);
    or detect_any_high(any_high, a, b);
    and make_reset_latch_n(reset_latch_n, reset_n, any_high);
    nand sr_set(q, set_latch_n, q_bar);
    nand sr_reset(q_bar, reset_latch_n, q);
endmodule
