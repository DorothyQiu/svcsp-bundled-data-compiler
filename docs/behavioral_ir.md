# Behavioral CSP IR

Phase 2 lowers a Phase 1 frontend report into a syntax-independent behavioral
process tree. Use `lower_behavioral(parse_text(source))` or
`lower_behavioral(parse_file(path))`.

The process nodes are `Sequence`, `Parallel`, `If`, `Send`, `Receive`,
`Assign`, and `Skip`. `BehavioralModule` owns the module name, declared
`ChannelEndpoint` and `Variable` identities, and the process body. A variable
identity includes its lexical scope and declaration source location. A channel
endpoint includes its declared channel name and any array selectors. Nodes and
symbolic `Expression` values carry `SourceLocation` where available.

`Sequence` preserves begin/end and statement order. `Parallel` preserves each
fork/join branch. `If` retains both source branches; an absent else becomes
`Skip`. Send and Receive remain inside their enclosing `If` nodes. Expressions
are symbolic `Expression` values and are neither evaluated nor lowered to RTL.

This IR has no pyslang objects and makes no decisions about communication
normalization, enable signals, dummy tokens, dependencies, pipelines,
controllers, storage, delays, or RTL generation.
