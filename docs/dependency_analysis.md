# Dependency Analysis

Phase 4 consumes a Phase 3 `NormalizedModule` with
`analyze_dependencies(module)` and returns a syntax-independent
`DependencyGraph`.

`DependencyNode` has a unique graph identity and traces to its source
behavioral operation, normalized wrapper, `ChannelEndpoint`, `Variable`,
`Enable`, and source location when applicable. Node kinds are ordinary
operations, `if` control points, parallel join points, enables, and normalized
communication wrappers.

Edges are explicitly classified as `DATA`, `SEQUENCE`, `CONTROL`,
`COMMUNICATION`, or `PARALLEL_JOIN`. Parallel branches begin independently;
their exits converge at a join node before subsequent sequential work. A
conditional wrapper is connected to its enable and the BODY `Skip` that replaced
the conditional external operation.

Conditional-receive definitions carry the symbolic validity guard of their
`Enable`. Before a `DATA` edge is created, the consumer's control-path guard
must contain every required branch literal. The analysis accepts conjunctions
of those literals and fails closed when implication cannot be proven. This
prevents `DummyToken(data_is_valid=False)` from reaching a consumer outside an
enabled path.

This pass does not schedule work, select pipeline stages or controllers,
allocate storage, model delays, or produce RTL.
