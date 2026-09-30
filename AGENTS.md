# Project Goal

Compile the supported SVCSP subset to synthesizable asynchronous RTL.

The current compiler architecture is defined by:

```text
docs/compiler_design.md
```

This document is the normative architecture specification for new compiler work.

---

# Architecture Authority

Before modifying compiler architecture or implementing a new compiler stage, read:

```text
docs/compiler_design.md
```

If existing implementation code or older documentation conflicts with
`docs/compiler_design.md`, follow `docs/compiler_design.md`.

Do not infer the intended architecture from legacy Python, RTL, tests, or older
compiler-flow documents.

Do not preserve a legacy abstraction solely because existing code depends on it.

When a conflict is found between the current implementation and the design
specification, report the conflict explicitly rather than silently adapting the
new design to the old code.

---

# Legacy Documentation

The repository contains older compiler and backend documentation, including:

```text
docs/compiler_flow.md
docs/communication_decomposition.md
docs/four_phase_bundled_data_backend.md
docs/supported_architectures.md
docs/verification_plan.md
```

These documents may still contain useful:

```text
source-language restrictions
previous backend semantics
test requirements
RTL implementation details
historical design rationale
```

However, they are not authoritative for the new compiler architecture when they
conflict with `docs/compiler_design.md`.

In particular, do not assume that the new compiler must preserve:

```text
Behavioral CSP IR
Transaction Extraction
Conditional Communication Decomposition
BODY / Enable / EN_RECV / EN_SEND
fixed M1-M7 phase ownership
```

unless their use is independently justified by the current design specification.

---

# Compiler Architecture Rules

The intended high-level flow is:

```text
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

Use `pyslang` for SystemVerilog semantic parsing.

Parse and semantically resolve source once. Reuse the resolved semantic context
in later stages rather than independently reparsing source text.

Preserve:

```text
Channel identity
variable identity
parameter identity
expression semantics
type and width information
source ordering
source locations when useful
```

Fail closed when required semantics cannot be resolved.

Do not silently resize, truncate, extend, reinterpret, or otherwise change
source behavior.

---

# USG Rules

The initial core USG node kinds are:

```text
Receive
Send
Assign
Predicate
```

The initial core dependency edge kinds are:

```text
DATA
CONTROL
```

The USG represents source semantics and dependencies.

The USG is not:

```text
a netlist
an asynchronous controller graph
a pipeline-stage graph
a template instance graph
a complete SystemVerilog AST
```

Do not introduce hardware placement decisions during graph construction.

DATA and CONTROL edges do not directly imply stage boundaries or physical
handshake structures.

Preserve communication ordering separately from ordinary data dependence.

For an assignment such as:

```systemverilog
x = x + a;
```

do not create an artificial Assign self-loop merely because `x` appears on both
sides.

Old/new value semantics are handled through source ordering, definition/use
analysis, and later Pipeline + State Planning.

---

# Planning and Backend Rules

Pipeline + State Planning owns decisions such as:

```text
stage boundaries
operation placement
predicate placement
cross-stage values
required storage
state lifetime
```

Async Microarchitecture is the first layer that should contain explicit
asynchronous hardware organization.

It may contain concepts such as:

```text
stages
handshake structure
stage-local datapath
stage-local state
branch / merge structure
control selection
storage resources
matched-delay requirements
```

Template Binding maps the selected Async Microarchitecture onto concrete
handshake templates.

Do not move template-specific decisions into the frontend or USG merely because
the current RTL library makes that convenient.

RTL generation must realize an already selected architecture. It must not invent
new semantic dependencies, storage requirements, or communication ordering.

---

# Synthesizability and Realizability

Keep these concepts separate:

```text
source / language legality
backend realizability
generated RTL validation
```

A construct may be legal synthesizable SystemVerilog but unsupported by the
current asynchronous backend.

Reject unsupported constructs explicitly.

Where practical, reuse existing synthesis or lint tools for final validation
instead of implementing a complete SystemVerilog synthesizability checker.

---

# Reuse of Existing Code

The existing implementation is a reference and source of reusable components,
not the architecture specification.

Reuse existing code when its semantics match `docs/compiler_design.md`.

Potentially reusable assets include:

```text
pyslang frontend utilities
semantic-resolution helpers
expression and width handling
source examples
tests and fixtures
simulation infrastructure
RTL libraries
backend implementation ideas
```

Do not broadly refactor legacy code before determining whether it belongs in the
new architecture.

Prefer building the new path incrementally alongside the legacy implementation.

---

# Development Rules

Work in small, independently testable steps.

Before implementing a task:

```text
1. read the relevant section of docs/compiler_design.md
2. inspect only the implementation needed for the current task
3. identify reusable code and legacy conflicts
4. add or update focused tests
5. make the smallest implementation change
6. run focused tests
7. run broader regression when appropriate
```

Do not redesign a later compiler stage merely to make an earlier-stage test pass.

Do not introduce a new compiler IR, graph abstraction, stage abstraction, or
backend semantic rule without a clear need justified against
`docs/compiler_design.md`.

If the specification leaves an architectural question open, report the question
rather than silently choosing a legacy behavior.

---

# Development Commands

Activate the repository environment with:

```bash
source .venv/bin/activate
```

Run tests with:

```bash
pytest -q
```

Before committing:

```bash
git diff --check
pytest -q
```

Use focused test invocations during development whenever possible.

---

# Implementation-Agent Reporting

After each implementation task, report:

```text
changed files
tests added or modified
tests executed
test results
legacy-code conflicts found
design questions or ambiguities found
```

Do not hide unresolved architecture conflicts by adding compatibility behavior.
