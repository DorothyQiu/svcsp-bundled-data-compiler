# Dependency Analysis

Phase 4 consumes a Phase 3 `NormalizedModule` with
`analyze_dependencies(module)` and returns a syntax-independent
`DependencyGraph`.

Node kinds include:

- `OPERATION`
- `CONTROL`
- `PARALLEL_JOIN`
- `ENABLE`
- `WRAPPER`

Edge kinds include:

- `DATA`
- `SEQUENCE`
- `CONTROL`
- `COMMUNICATION`
- `PARALLEL_JOIN`

Parallel branches begin independently and converge at a join node before
subsequent sequential work.

## Conditional Communication Identity

Phase 4 associates conditional communication semantics through the exact
shared Phase 3 `CommunicationSite`.

It does not recover wrapper association by:

- matching a behavioral `Skip`;
- matching source locations.

For each conditional site, Phase 4 validates the shared identity of:

- `CommunicationSite`;
- `BodyChannel`;
- `Enable`;
- BODY communication;
- normalized wrapper.

The Phase 4 graph retains a compatibility operation label where required by
later phases, but semantic association is based on the exact
`CommunicationSite` object, not on `Skip` inheritance.

## Communication Edge Direction

The `COMMUNICATION` edge records BODY/wrapper token flow:

- conditional Receive: wrapper -> communication site;
- conditional Send: communication site -> wrapper.

Phase 5 uses this topology to validate wrapper attachment.

## Blocking Ordering

Original SVCSP communication is blocking.

For a conditional Send, later source operations must be sequenced after
normalized Send-wrapper completion.

This is stronger than sequencing after BODY-to-wrapper token handoff:
internal token handoff does not mean an enabled external `Send` has completed.

The decomposed-SVCSP completion Channel is one executable realization of this
ordering requirement. The Phase 4 dependency graph itself already represents
the required ordering.

For conditional Receive, an enabled external Receive completes before the
wrapper can provide the real BODY-side token.

## Conditional Receive Validity

Conditional-receive definitions carry the symbolic validity guard of their
shared `Enable`.

A reaching definition records both the producing node and its validity guard.

Before a `DATA` dependency is accepted, the consumer control-path guard must
imply the producer validity guard.

The analysis fails closed when that implication cannot be established.

This prevents `DummyToken(data_is_valid=False)` data from reaching consumers
outside the conditional path where the external Receive actually occurred.

## Non-Goals

Phase 4 does not:

- schedule hardware;
- select pipeline stages;
- select controllers;
- allocate storage;
- choose matched delays;
- emit RTL.
