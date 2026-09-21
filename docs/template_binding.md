# Structural Template Binding

Phase 7A consumes a Phase 6 `MicroarchitectureGraph` with
`bind_templates(graph)` and returns a syntax-independent `BoundStructuralGraph`.
It records bindings to abstract structural templates, explicit interface
contracts, logical signals, and indexed port maps; it does not generate
SystemVerilog.

Each BODY stage becomes a `BoundBodyStage`: `LINEAR` binds to
`linear_controller` and `JOIN` binds to `join_controller`. Conditional
communication remains separate in `BoundWrapper`: `CONDITIONAL_RECV` binds to
`conditional_recv_wrapper`, and `CONDITIONAL_SEND` binds to
`conditional_send_wrapper`. A wrapper retains its attachment to the BODY stage,
channel endpoint, enable identity, and source location without replacing the
BODY controller.

Storage is bound as `abstract_storage` only when the stage's
`StorageRequirement.required` is true. A `MatchedDelayRequirement` binds as a
`symbolic_matched_delay` only when Phase 6 supplied one. These bindings retain
their symbolic requirements and make no FF/latch selection or physical delay
calculation.

Each `TemplateContract` contains formal `TemplatePort` definitions with a name,
role, direction, semantic kind, and connection multiplicity. The semantic kinds
are handshake request, handshake acknowledge, payload, enable, and local
control. A `maximum` of `None` is a topology-sized port family, represented by
indexed `BoundPortBinding` objects rather than a concrete HDL array.

`linear_controller` has upstream and downstream request/acknowledge families,
payload input/output families, and one local-control port. `join_controller`
uses the same handshake roles; its upstream bindings are derived directly from
the incoming PipelineGraph topology, so the binding records its actual fan-in.

`conditional_recv_wrapper` binds external request/acknowledge/data as inputs to
the wrapper and BODY-side request/acknowledge/data toward the attached stage.
`conditional_send_wrapper` reverses that BODY/external data-flow direction.
Both bind their exact `Enable` identity. Wrapper bindings remain separate from
the attached BODY controller binding.

`abstract_storage` has data input/output and stage-control input/output ports.
`symbolic_matched_delay` has control input/output ports and retains the Phase 6
symbolic requirement. The bound graph adds these instances only when their
Phase 6 requirements exist.

`BoundLogicalSignal` identifies every connection without Verilog syntax. It can
refer to a source/target stage, dependency kind, channel endpoint, variable,
symbolic expression, enable, source location, `PayloadType`, and `PayloadWidth`.
Every payload signal receives its declared concrete or symbolic width from the
single Phase 1/2 type representation. Symbolic widths retain their owning
parameter identity. Request, acknowledge, enable, and control signals have the
shared one-bit width. `BoundPortBinding` connects a specific
formal port index to one such signal. This preserves BODY operations, symbolic
expressions, variable and endpoint identity, handshake topology, dependencies,
metadata, and source locations for mechanical Phase 7B emission. Binding fails
closed when a payload port has no established declared or contextual width, an
invalid width representation, or a channel/variable/storage/wrapper payload
connection whose widths cannot be proven equal. It does not insert a cast,
truncation, extension, or resize.

The payload type already reflects Phase 2's conservative expression-width
rules: data bit selects are one bit, provable part selects carry their selected
width, and only compatible binary or conditional value expressions carry a
width. Channel array selectors remain endpoint identity only and do not alter
payload width.
Controller implementation, storage primitives, timing values, optimization,
and RTL generation remain outside this pass.
