# End-to-end linear MVP

`compile_linear_file(path)` composes the existing frontend, Behavioral IR,
communication normalization, dependency analysis, conservative pipeline
synthesis, microarchitecture selection, template binding, and structural RTL
emission passes. It accepts only the single-predecessor/single-successor linear
MVP: fork/join, normalized conditional communication wrappers, and other
non-linear pipeline topologies fail closed.

`tests/fixtures/linear_receive_add_send.sv` exercises an 8-bit transaction:
`A.Receive(a)`, `b = a + c`, then `B.Send(b)`. The end-to-end test compiles the
fixture to temporary structural RTL, elaborates it with all MVP library sources
using `iverilog -g2012`, and runs it with `vvp` when Icarus is available.

The test drives `A` through a full four-phase input handshake with `a=5`, holds
the independently declared 8-bit addend `c=3`, and observes `B`. It checks at
the rising `B` request that payload is already stable and equals `8`. It then
completes the reciprocal `B` acknowledge cycle. The generated RTL contains the
existing `linear_controller`, `abstract_storage #(.WIDTH(8))`, and
`symbolic_matched_delay` templates.
