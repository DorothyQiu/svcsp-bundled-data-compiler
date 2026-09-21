# Behavioral CSP IR

Phase 2 lowers a Phase 1 frontend report into a syntax-independent behavioral
process tree. Use `lower_behavioral(parse_text(source))` or
`lower_behavioral(parse_file(path))`.

The process nodes are `Sequence`, `Parallel`, `If`, `Send`, `Receive`,
`Assign`, and `Skip`. `BehavioralModule` owns the module name, declared
`ChannelEndpoint` and `Variable` identities, and the process body. A variable
identity includes its lexical scope, declaration source location, and a
syntax-independent `PayloadType`. `PayloadType` contains a `PayloadWidth` that
is either concrete or symbolic plus the optional packed range. Symbolic widths
carry the exact owning `Parameter` declaration identity, including its module,
location, and symbolic default expression. A channel
endpoint includes its declared channel name, any array selectors, and resolved
payload context. Nodes and symbolic `Expression` values carry `SourceLocation`
where available.

`Sequence` preserves begin/end and statement order. `Parallel` preserves each
fork/join branch. `If` retains both source branches; an absent else becomes
`Skip`. Send and Receive remain inside their enclosing `If` nodes. Expressions
are symbolic `Expression` values and are neither evaluated nor lowered to RTL.

Send and Receive endpoint payload context uses a declared channel payload type
when available, otherwise the declared Receive target or the common declared
operand type of a Send expression. When both widths are known, they must be the
same concrete bit count or the same symbolic expression with the same owned
parameter identities. No SystemVerilog expression sizing, cast, truncation, or
extension is performed. An endpoint whose payload remains unknown is
representable here but will fail closed when a later structural binding requires
its width.

Payload expression width inference is deliberately conservative. Variable
references use their declaration type; packed bit selects are one bit; and
concrete or owned-symbolic part selects use the proven selected width. Unary
width-preserving operators retain their operand width, reductions, comparisons,
and logical operations are one bit, and binary or conditional value expressions
require exactly compatible operand or branch widths. Literal and any other
unproven expression widths remain unknown. A known Send channel never supplies
an otherwise unknown expression width. Assignments apply the same rule: the
RHS must independently establish a width and then exactly match the declared
target width (including the width of a selected target). The assignment target
never supplies a fallback width, so the IR never relies on implicit resizing,
truncation, extension, or casts.

This IR has no pyslang objects and makes no decisions about communication
normalization, enable signals, dummy tokens, dependencies, pipelines,
controllers, storage, delays, or RTL generation.
