# Conservative Pipeline Synthesis

Phase 5 consumes a `DependencyGraph` with `synthesize_pipeline(graph)` and
returns a syntax-independent `PipelineGraph`.

## Current Linear Grouping

For the supported unconditional linear form:

    Receive;
    Assign*;
    Send;

the transaction becomes one BODY pipeline stage.

Receive and Send are the upstream/downstream communication boundaries.

The Assign operations form the combinational BODY datapath.

They are not separate handshake stages.

Other Phase 4 `OPERATION` nodes retain the conservative stage representation
required by the current implementation. Every `PARALLEL_JOIN` becomes a
`JOIN` stage.

`CONTROL` and `ENABLE` nodes remain metadata constraints rather than datapath
stages.

Every Phase 4 edge becomes a `PipelineDependency`, retaining:

- dependency kind;
- source dependency-node identity;
- target dependency-node identity;
- source stage identity when applicable;
- target stage identity when applicable.

## Conditional Wrapper Attachment

`WrapperAttachment` connects each normalized external wrapper to the exact
BODY-side `CommunicationSite` operation node representing that conditional
occurrence.

Attachment is validated using:

- exact object identity;
- Phase 4 `COMMUNICATION` topology.

It does not use:

- behavioral `Skip` inheritance;
- source-location matching.

The expected communication direction is:

- conditional Receive: wrapper -> site;
- conditional Send: site -> wrapper.

Attachment fails closed when:

- the site is absent;
- multiple candidate sites exist;
- the expected communication edge is absent or duplicated;
- the communication direction is wrong.

## Ordering Preservation

All Phase 4 `SEQUENCE` dependencies are preserved in the `PipelineGraph`.

This includes dependencies requiring continuation after conditional-Send
wrapper completion.

Pipeline synthesis must not reinterpret BODY-to-wrapper token handoff as
completion of the original blocking external Send.

## Metadata

`PipelineMetadata` retains relevant `CONTROL`, `ENABLE`, and `WRAPPER`
identities without turning them into datapath stages.

## Non-Goals

Phase 5 does not select:

- controller circuits;
- storage primitives;
- handshake circuits;
- delay implementations;
- structural RTL;
- optimization strategies.
