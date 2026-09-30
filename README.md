# SVCSP Bundled-Data Compiler

Research compiler for translating a supported SystemVerilog CSP (SVCSP) subset
into synthesizable asynchronous RTL.

The compiler is currently being rebuilt around a semantic-graph-based
architecture.

## Compiler architecture

```text
SystemVerilog / SVCSP
        |
        v
pyslang Semantic Frontend
        |
        v
Unified Semantic Graph (USG)
        |
        v
Legality + Dependency Analysis
        |
        v
Pipeline + State Planning
        |
        v
Async Microarchitecture
        |
        v
Template Binding
        |
        v
Synthesizable SystemVerilog
```

The authoritative architecture specification is:

```text
docs/compiler_design.md
```

Implementation-agent instructions are in:

```text
AGENTS.md
```

## Current status

The implementation is being rebuilt from a clean architecture boundary.

The first implementation milestones are:

```text
1. native pyslang semantic frontend
2. minimal USG representation
3. Receive / Send / Assign graph construction
4. Predicate and CONTROL construction
5. legality and dependency analysis
6. pipeline and state planning
7. asynchronous microarchitecture
8. template binding and RTL generation
```

The previous M1–M7 compiler implementation is preserved in Git history at:

```text
legacy-m1-m7
```

It may be consulted as a reference, but it is not the architecture
specification for the current compiler.

## Development environment

Activate the repository environment with:

```bash
source .venv/bin/activate
```

Run tests with:

```bash
pytest -q
```

The project currently requires:

```text
Python >= 3.10
pyslang >= 11,<12
pytest >= 7  (test dependency)
```

## Repository layout

```text
docs/
    compiler_design.md

src/
    svcsp_compiler/

tests/
```

Additional compiler stages and tests will be added incrementally as the new
architecture is implemented.
