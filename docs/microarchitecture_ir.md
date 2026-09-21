# Bundled-Data Microarchitecture IR

Phase 6 consumes a Phase 5 `PipelineGraph` with
`select_microarchitecture(graph)` and returns a syntax-independent
`MicroarchitectureGraph`.

Each `MicroarchitectureStage` contains symbolic BODY operations, a BODY
controller template kind, storage requirement, symbolic handshake ports, and an
optional symbolic `MatchedDelayRequirement`. Pipeline dependencies and metadata
are retained without changing topology.

`OPERATION` stages select `LINEAR`; `JOIN` stages select `JOIN`. A grouped
unconditional `Receive; Assign*; Send` BODY stage has one LINEAR controller:
the Receive and Send are its external handshake boundaries, while Assigns are
its combinational datapath, with no internal handshake between them.
`MicroarchitectureWrapper` separately selects `CONDITIONAL_RECV` for an
`into_body` attachment and `CONDITIONAL_SEND` for a `from_body` attachment. It
retains wrapper identity, BODY-stage identity, `ChannelEndpoint`, `Enable`, and
source location without replacing the BODY controller.

`combinational_logic` records nontrivial symbolic BODY expressions. This includes
an `Assign` RHS, an unconditional `Send` value, and a conditional Send value
attached to its BODY-side stage. The conditional wrapper controls external
communication only; it does not own value evaluation or matched-delay intent.

Datapath BODY stages have symbolic required storage. JOIN and `Skip` anchors
without combinational BODY logic are topology-only and do not require datapath
storage or a matched-data-path delay. A symbolic matched delay is created only
when `combinational_logic` is present. This is template selection only: no
controller circuit, C-element, req/ack signal, storage primitive, delay value,
or RTL is created.
