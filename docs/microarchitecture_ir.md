# Bundled-Data Microarchitecture IR

Phase 6 consumes a Phase 5 `PipelineGraph` with
`select_microarchitecture(graph)` and returns a syntax-independent
`MicroarchitectureGraph`.

Each `MicroarchitectureStage` contains:

- symbolic BODY operations;
- BODY controller kind;
- storage requirement;
- symbolic handshake ports;
- optional symbolic `MatchedDelayRequirement`.

Pipeline dependencies and metadata are retained without changing dependency
topology.

## BODY Controllers

`OPERATION` stages select `LINEAR`.

`JOIN` stages select `JOIN`.

A grouped unconditional:

    Receive;
    Assign*;
    Send;

transaction has one LINEAR BODY controller.

Receive and Send are its external handshake boundaries.

The Assign operations form its combinational datapath.

No internal handshake stage is inserted between those operations.

## Conditional Wrappers

`MicroarchitectureWrapper` is separate from the BODY controller.

Wrapper kinds include:

- `CONDITIONAL_RECV`;
- `CONDITIONAL_SEND`.

A wrapper retains:

- wrapper identity;
- attached BODY-stage identity;
- `ChannelEndpoint`;
- `Enable`;
- source location.

Conditional-wrapper topology originates from the exact Phase 3
`CommunicationSite` identity propagated through Phase 4 and Phase 5.

A communication site may act as a topology/control anchor without itself being
a datapath operation.

## Datapath and Matched Delay

`combinational_logic` records nontrivial symbolic BODY expressions.

This may include:

- Assign RHS expressions;
- unconditional Send values;
- conditional Send values associated with the BODY-side stage.

The conditional wrapper controls whether external communication occurs.
It does not own BODY value evaluation.

Datapath BODY stages may require symbolic storage.

A symbolic matched-delay requirement is created only when the current
microarchitecture rules identify combinational BODY logic requiring one.

## Phase Boundary

Phase 6 selects symbolic microarchitecture only.

It does not create:

- concrete controller circuits;
- C-elements;
- final req/ack wiring;
- concrete storage cells;
- physical delay values;
- structural RTL.
