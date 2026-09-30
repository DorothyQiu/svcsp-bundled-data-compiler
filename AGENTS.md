# Project Goal

Compile the supported SVCSP subset into synthesizable asynchronous RTL.

The compiler is being rebuilt from a clean architecture boundary.

# Architecture Authority

Before modifying compiler architecture or implementing a compiler stage, read:

```text
docs/compiler_design.md
```

`docs/compiler_design.md` is the normative architecture specification.
Verification strategy is defined in `docs/test_plan.md`.

The previous M1–M7 implementation is preserved at:

```text
legacy-m1-m7
```

Historical code may be consulted for algorithms, tests, implementation ideas,
or backend experiments, but it is not authoritative for the current compiler.

If historical code conflicts with `docs/compiler_design.md`, follow the design
specification.

Do not reintroduce a legacy abstraction solely because it existed before.

# Compiler Architecture

The intended flow is:

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

Keep compiler stages separated according to this architecture.

# Semantic Frontend Rules

Use `pyslang` for parsing and semantic resolution.

Parse and semantically resolve source once.

Reuse the resolved native pyslang semantic context rather than converting the
syntax tree into an independent pseudo-semantic representation.

Preserve:

```text
Channel identity
variable identity
parameter identity
expression semantics
resolved types and widths
source ordering
source locations when useful
```

Fail closed when required semantics cannot be resolved.

Do not silently resize, truncate, extend, reinterpret, or otherwise change
source behavior.

Avoid introducing another behavioral IR between pyslang semantics and the USG
unless a concrete requirement is identified and documented.

# USG Rules

Initial core node kinds:

```text
Receive
Send
Assign
Predicate
```

Initial dependency edge kinds:

```text
DATA
CONTROL
```

The USG represents source semantics and dependencies.

It is not:

```text
a complete SystemVerilog AST
a netlist
an asynchronous controller graph
a pipeline-stage graph
a template-instance graph
```

Graph construction must not make asynchronous hardware-placement decisions.

DATA and CONTROL edges do not directly imply stage boundaries or handshake
structures.

Communication ordering is semantically significant and must be preserved
separately from ordinary DATA dependence.

For:

```systemverilog
x = x + a;
```

do not create an artificial Assign self-loop.

The RHS consumes the earlier value of `x`; the LHS defines the later value.
Definition/use ordering and later Pipeline + State Planning preserve this
semantic distinction.

# Legality and Analysis Rules

Keep these concepts distinct:

```text
source / language legality
semantic dependency analysis
backend realizability
generated RTL validation
```

A legal synthesizable SystemVerilog construct may still be unsupported by the
current asynchronous backend.

Reject unsupported behavior explicitly rather than forcing it into an existing
hardware pattern.

# Planning Rules

Pipeline + State Planning owns decisions including:

```text
stage boundaries
operation placement
predicate placement
cross-stage values
required storage
state lifetime
datapath-to-control relationships
```

DATA dependence alone does not automatically imply a stage boundary.

When a value must survive across a communication or stage boundary, planning
must explicitly preserve it.

Do not assume stage-local datapaths are always purely combinational.

# Async Microarchitecture Rules

Async Microarchitecture is the first layer that should contain explicit
asynchronous hardware organization.

It may represent:

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

Keep this representation independent enough from a specific RTL template
family to permit alternative asynchronous implementations.

# Template Binding and RTL Rules

Template Binding maps an already selected Async Microarchitecture onto concrete
asynchronous templates.

Template-specific decisions must not leak backward into the frontend or USG.

RTL generation realizes the selected architecture; it must not invent semantic
dependencies, communication ordering, or storage requirements.

# Historical Code

Historical files can be inspected with:

```bash
git show legacy-m1-m7:<path>
```

Potentially useful historical material includes:

```text
pyslang experiments
expression and width algorithms
test scenarios
semantic-analysis algorithms
RTL templates
simulation infrastructure
backend experiments
```

Do not copy a historical API or data model merely because one of its internal
algorithms is useful.

The following historical abstractions are not automatically part of the new
compiler:

```text
Behavioral CSP IR
Transaction Extraction
Conditional Communication Decomposition
BODY / Enable / EN_RECV / EN_SEND
fixed M1-M7 phase ownership
```

# Development Rules

Work in small, independently testable steps.

Before implementing a task:

```text
1. read the relevant section of docs/compiler_design.md
2. inspect only the material needed for the current task
3. identify the required semantic contract
4. add or update focused tests
5. make the smallest implementation change
6. run focused tests
7. run broader regression when appropriate
```

Do not introduce a new compiler IR, graph abstraction, stage abstraction, or
backend semantic rule without a concrete need justified against
`docs/compiler_design.md`.

If the design specification leaves a question open, report it instead of
silently choosing historical behavior.

# Development Commands

```bash
source .venv/bin/activate
pytest -q
```

Before committing:

```bash
git diff --check
pytest -q
```

# Implementation-Agent Reporting

After each implementation task, report:

```text
changed files
tests added or modified
tests executed
test results
historical-code conflicts found
design questions or ambiguities found
```

Do not hide unresolved architectural conflicts by adding compatibility behavior.
