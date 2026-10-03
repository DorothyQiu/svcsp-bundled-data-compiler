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
Lcarry   left sideband payload

Rreq     right request
Rack     right acknowledge
Rdata    right primary data payload
Rcarry   right sideband payload

click    local stage-storage capture event
reset    reset
```

Do not use `data_in`, `data_out`, `carry_in`, `carry_out`, or `fire` in the
RTL-library interface or generated RTL.

## Coding Rules

- Asynchronous control templates are structural and topology-preserving.
- Control, interconnect, and datapath connections use `wire`.
- Wrappers and generated control must not infer flip-flops or latches.
- Stage storage is represented by an explicit storage module.
- Persistent state uses separate, explicit state storage; it is not stage
  carry payload.
- `Lcarry` and `Rcarry` are sideband payload associated with the same stage
  handshake, not an independent channel.
- `Rdata` and `Rcarry` are captured by the same stage storage on `click`.
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
Lcarry ----/                                  |
                                              -> Rcarry
```

`Lcarry` and `Rcarry` carry compiler-planned cross-phase `DATA` and `CONTROL`
values. They have no independent request/acknowledge handshake and must pass
through stage storage; they must never bypass it. Persistent state is not
carried through `Lcarry` or `Rcarry`.

A value that survives more than one logical boundary is stored and forwarded
through every intervening stage. For example:

```systemverilog
A.Receive(a);
B.Send(a);
C.Receive(c);
D.Send(a + c);
```

Planning requires `a` to survive `P0 -> P1`, so `a` is represented in the
`P0 Rcarry` / `P1 Lcarry` payload.

## Planned Library Components

The following component paths are planned, but their internals are not defined
by this document:

```text
rtl_lib/controllers/basic_click_ctrl.sv
rtl_lib/controllers/phase_decoupled_click_ctrl.sv
rtl_lib/stages/linear_stage.sv
rtl_lib/storage/stage_storage.sv
rtl_lib/delay/matched_delay.sv
```

The exact controller implementations and port definitions are pending the
gate-level template definitions.
