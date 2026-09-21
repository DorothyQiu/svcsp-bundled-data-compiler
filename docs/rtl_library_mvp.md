# MVP RTL library

The MVP library implements one bundled-data variant only: a four-phase linear
half-buffer. The generated template identities `linear_controller`,
`abstract_storage`, and `symbolic_matched_delay` are thin bindings to the MVP
modules `four_phase_linear_controller`, `transparent_latch`, and
`matched_delay`.

`four_phase_linear_controller` has `lreq` and `rack` inputs and `lack`,
`latch_en`, and `raw_rreq` outputs. Its state is initially low. It behaves as a
C-element over `lreq` and `!rack`: it rises when `lreq=1,rack=0`, falls when
`lreq=0,rack=1`, and holds in the mixed input states. `lack`, `latch_en`, and
`raw_rreq` all equal this state. It has no payload ports.

`transparent_latch` is parameterized by `WIDTH`. It passes `data_in` while
`en` is high and retains its prior `data_out` while `en` is low.

For a BODY stage with a symbolic matched-delay binding, `linear_controller`
exports the four-phase controller's raw request on `local_control`. The bound
`symbolic_matched_delay` receives that signal, and its delayed output returns
on `delayed_control`; `linear_controller` uses only that delayed return to
drive `downstream_req_0`. The raw request never bypasses the delay. A stage
with no matched-data-path requirement explicitly binds `delayed_control` to
the raw control signal instead.

`matched_delay` is parameterized by an explicit simulation delay and propagates
logic transitions after that delay. Its source file explicitly uses a `1ns/1ps`
simulation timescale, so `DELAY=3` means three nanoseconds. It is a
simulation-oriented model, not a technology-mapped or synthesizable physical
delay line.

JOIN, conditional send/receive wrappers, arbitration, semi-decoupled and
fully-decoupled controllers, Click elements, CP latches, alternate storage,
and physical delay-chain synthesis are unsupported future variants.
