# RTL Library Coding Contract

This document defines the coding contract for the asynchronous RTL library.
It applies to library templates and the compiler-generated RTL that binds to
them. The library realizes an already selected Async Microarchitecture; it
does not introduce semantic dependencies, communication ordering, storage, or
state requirements.

## Naming

Use these signal names for the left and right interfaces of a stage:

```text
Lreq     left request
Lack     left acknowledge
Ldata    left primary data payload

Rreq     right request
Rack     right acknowledge
Rdata    right primary data payload

click    local stage-storage capture event
reset_n  active-low asynchronous reset
```

Do not use `data_in`, `data_out`, `carry_in`, `carry_out`, or `fire` in the
RTL-library interface or generated RTL.

## Coding Rules

- Asynchronous control templates are structural and topology-preserving.
- Control, interconnect, and datapath connections use `wire`.
- Wrappers and generated control must not infer flip-flops or latches.
- Stage storage is represented by an explicit storage module.
- Persistent state uses separate, explicit state storage.
- `Rdata` is captured by stage storage on the positive edge of `click`.
- `click` is the local positive-edge clock event, never a clock enable on
  another clock. Basic Click stages have no global or local clock input.
- By default, only Click-controller state is reset. Basic Click state resets
  to `0` using the active-low asynchronous `reset_n` input.
- `Lreq` and `Rack` must remain at four-phase idle `0` while reset is asserted
  and during reset release. Reset must not be added as gating to `click`.
- Stage data storage has no reset.
- Persistent-state reset is source- and architecture-dependent.
- Compiler-generated combinational datapath may be synthesis-optimized.
- Controller topology should remain structurally identifiable after synthesis.

## First Template: Basic Linear Micropipeline Stage

The first template is a basic linear micropipeline stage with the following
conceptual structure:

```text
Lreq -> DelayLine -> Click controller -> Rreq
Lack <-              Click controller <- Rack
                            |
                           click
                            |
                            v
Ldata  ----\\
            -> combinational datapath -> Stage Storage -> Rdata
```

Basic and linear channel interfaces use only:

```text
Lreq / Lack / Ldata
Rreq / Rack / Rdata
```

The matched delay is a parameterized, structural buffer chain on `Lreq` before
the controller. The Basic Click controller inputs are delayed `Lreq`, `Rack`,
and `reset_n`; its outputs are `Lack`, `Rreq`, and `click`. It has exactly one
controller-state flip-flop:

```text
D    = ~Q
Q    = Lack
Rreq = Lack
```

The active-low asynchronous `reset_n` initializes `Q` and `Lack` to `0`. The
flip-flop updates on the positive edge of `click`, where `click` is generated
combinationally as:

```text
(~Lreq & Lack & Rack) |
( Lreq & ~Lack & ~Rack)
```

Stage data storage is an explicit, unreset flip-flop bank clocked by the
positive edge of `click`; `click` is not an enable on a separate clock.
Persistent state, when required, uses separate explicit state storage whose
reset behavior is selected by the source and architecture.

A value that survives more than one logical boundary is stored and forwarded
through every intervening stage. For example:

```systemverilog
A.Receive(a);
B.Send(a);
C.Receive(c);
D.Send(a + c);
```

The logical plan is `P0 = A.Receive / B.Send`, `P1 = C.Receive / D.Send`,
with boundary survival `{a}`. When pipeline realization is selected, one
compiler-generated bundled-data internal channel carries `a` from P0 to P1.
Its `req` and `ack` carry phase progression and completion; its `data` packs
all survival values for the boundary. This does not widen either external
source-level channel payload.

P1 has two handshaked inputs: the compiler-generated phase channel carrying
`a`, and source channel `C` carrying `c`. It therefore requires a Join-type
input topology rather than a simple linear stage.

## Hardware-Topology Classification

- Zero upstream handshaked channels: Source-type candidate.
- One upstream handshaked channel: linear/Reg-type candidate.
- Multiple upstream handshaked channels: Join-type structure.

Compiler-generated internal channels count as upstream and downstream channels
exactly like source-level channels for this classification.

## Source-type Click

A Source-type controller is selected only when a region has zero upstream
handshaked channels. Its controller interface contains only `Rack`, `Rreq`,
and `reset_n`; it has no `Lreq`, `Lack`, `Ldata`, `Iport`, or carry ports.
It has one active-low asynchronously reset `click_dff_reset_n`, `Po`, whose
output is `Rreq` and whose input is `~Rreq`.

```text
click = Rreq XNOR Rack
```

The compiler-generated datapath produces the outgoing payload. Its result is
captured by `stage_storage` on the same positive edge of `click` and becomes
`Rdata`. If a compiler-generated internal phase channel feeds the region, that
channel is an upstream handshaked channel and the region is no longer
Source-type.

## Sink-type Click

A Sink-type region has one or more upstream handshaked inputs and no downstream
handshaked output. The simple Sink controller is the one-upstream-input case.
Its interface contains only `Lreq`, `Lack`, and `reset_n`; it has no `Iport`
or `Oport`. It has one active-low asynchronously reset `click_dff_reset_n`,
`Pi`, whose output is `Lack` and whose input is `~Lack`.

```text
click = Lreq XOR Lack
```

The incoming payload is `Ldata`, and the matched delay remains on `Lreq`
before the controller. A Sink does not require generic `stage_storage` merely
because it is a Sink: terminal computation may consume `Ldata` directly.
Persistent-state updates use separate, explicit persistent-state storage. If a
compiler-generated internal channel leaves the region, the region has a
downstream handshaked output and is not a Sink.

## Phase-Decoupled Click v1

The current Phase-Decoupled Click v1 implementation assumption is a
two-state structural controller. `Pi` drives `Lack` and `Po` drives `Rreq`.
Each is an active-low asynchronously reset `click_dff_reset_n` with inverter
feedback (`D = ~Q`). `Pi` and `Po` both toggle on the same positive edge of
`click`.

```text
input_pending = Lreq XOR Lack
output_ready  = Rreq XNOR Rack
click         = input_pending AND output_ready
```

The controller has no global clock, behavioral FSM, or state beyond `Pi` and
`Po`.

## Planned Library Components

The following component paths define the initial library layout:

```text
rtl_lib/primitives/click_dff.sv
rtl_lib/primitives/click_dff_reset_n.sv
rtl_lib/controllers/basic_click_ctrl.sv
rtl_lib/controllers/phase_decoupled_click_ctrl.sv
rtl_lib/controllers/source_click_ctrl.sv
rtl_lib/controllers/sink_click_ctrl.sv
rtl_lib/stages/linear_stage.sv
rtl_lib/storage/stage_storage.sv
rtl_lib/delay/matched_delay.sv
```

The Basic Click, Phase-Decoupled Click v1, Source-type Click, and Sink-type
Click controllers, matched delay, and stage-storage primitives are defined.
Exact implementations and ports for the linear stage remain pending the
gate-level template definitions.
