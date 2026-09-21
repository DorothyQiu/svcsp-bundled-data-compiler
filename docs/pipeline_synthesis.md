# Conservative Pipeline Synthesis

Phase 5 consumes a `DependencyGraph` with `synthesize_pipeline(graph)` and
returns a syntax-independent `PipelineGraph`.

Every Phase 4 `OPERATION` becomes one `PipelineStage`; the `operations` tuple
allows later optimization to merge combinational operations without changing
the representation. Every `PARALLEL_JOIN` becomes a `JOIN` stage. `CONTROL`
and `ENABLE` nodes remain metadata constraints and do not become datapath
stages.

Every Phase 4 edge becomes a `PipelineDependency`, retaining its dependency
kind and the source/target dependency-node identities. When both endpoints are
stages, their stage identities are also recorded. `WrapperAttachment` connects
each normalized external wrapper to the BODY-side `Skip` stage that represents
its normalized communication occurrence. `PipelineMetadata` retains `CONTROL`,
`ENABLE`, and `WRAPPER` identities as constraint sources without creating
datapath stages.

This is a deliberately conservative partition only. It does not select a
controller, storage, handshake circuit, delay, RTL, or optimization strategy.
