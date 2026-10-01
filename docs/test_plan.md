# SVCSP Compiler Test Plan

## 1. Purpose

This document defines the verification strategy for the current SVCSP compiler architecture.

The architecture itself is defined in:

```text
docs/compiler_design.md
```

Tests should verify compiler-stage contracts and architectural invariants rather than preserve historical implementation behavior.

The previous M1–M7 test suite is available through:

```text
legacy-m1-m7
```

Historical tests may be mined for useful scenarios, but they are not automatically requirements for the current compiler.

---

## 2. Test Strategy

Use four levels of testing:

```text
unit tests
    ↓
stage-contract tests
    ↓
cross-stage integration tests
    ↓
generated-RTL / synthesis / simulation tests
```

Tests should be added incrementally with implementation.

Each compiler stage should have focused tests before it is connected to later stages.

---

## 3. Semantic Frontend

The semantic frontend must verify the contract:

```text
SystemVerilog source
        ↓
one SyntaxTree
        ↓
one pyslang Compilation
        ↓
resolved native semantic objects
```

Required coverage:

- syntax diagnostics are detected;
- semantic diagnostics are detected;
- the same SyntaxTree is added to the Compilation;
- an elaborated top instance is available;
- native `VariableSymbol` and `ParameterSymbol` objects are accessible;
- repeated lookup preserves native symbol identity;
- resolved expression symbols refer to the same native objects;
- resolved types and bit widths are available;
- procedural statement order matches source order;
- semantic objects retain syntax backlinks where applicable;
- source locations can be recovered through the retained `SourceManager`.

The frontend tests must not require:

- USG construction;
- Receive / Send recognition;
- compiler-owned Variable or Parameter classes;
- manual name resolution;
- manual width inference;
- asynchronous hardware decisions.

Additional frontend coverage includes:

- file input;
- preprocessing;
- include directories;
- macros;
- source-location behavior through preprocessing;
- multiple supported source files if required.

---

## 4. Unified Semantic Graph

USG tests should verify semantic graph construction independently of pipeline planning.

Initial required node coverage:

```text
Receive
Send
Assign
Predicate
```

Initial required edge coverage:

```text
DATA
CONTROL
```

Required invariants include:

- graph construction preserves native semantic identity;
- DATA edges represent actual value dependencies;
- CONTROL edges represent predicate control;
- communication source ordering is preserved separately from DATA dependence;
- `UnifiedSemanticGraph.nodes` preserves builder source or expanded semantic-occurrence order without fabricating a DATA edge;
- `x = x + a` does not create an artificial Assign self-loop;
- graph construction does not introduce stage boundaries;
- graph construction does not bind asynchronous templates;
- graph construction does not insert storage.

Initial straight-line fixture:

```systemverilog
A.Receive(a);
t = a + b;
B.Send(t);
```

Expected conceptual dependency:

```text
Receive(a)
    |
    | DATA
    v
Assign(t = a + b)
    |
    | DATA
    v
Send(t)
```

---

## 5. Legality and Dependency Analysis

Tests should distinguish:

```text
source / language legality
semantic dependency analysis
backend realizability
```

These must not be collapsed into one rejection mechanism.

Dependency tests should eventually cover:

- definition/use relationships;
- reaching values;
- source ordering;
- communication ordering;
- conditional definitions;
- selected-value dependencies;
- overlapping reads and writes;
- value availability and lifetime.

---

## 6. Pipeline and State Planning

Planning tests should verify implementation decisions without requiring final RTL.

Required areas include:

- communication-induced stage boundaries;
- operations remaining within a stage when legal;
- values crossing stage boundaries;
- required state insertion;
- predicate placement;
- predicate values crossing stage boundaries;
- preservation of communication ordering.

A DATA dependency alone must not automatically imply a new stage.

---

## 7. Async Microarchitecture

Tests should verify that a completed plan lowers to explicit asynchronous hardware organization.

Coverage should include:

- stages;
- handshake connectivity;
- stage-local datapath;
- stage-local state;
- branch and merge structure;
- control selection;
- required storage;
- matched-delay requirements when applicable.

These tests should not depend unnecessarily on one concrete RTL template family.

---

## 8. Template Binding and RTL

Template-binding tests should verify:

- correct template selection;
- port and signal connectivity;
- parameter and width propagation;
- state realization;
- deterministic naming;
- traceability from planned resources to generated RTL objects.

RTL tests should verify syntax and elaboration with downstream tools where available.

---

## 9. End-to-End Validation

As the compiler becomes vertically complete, add representative end-to-end programs covering:

```text
straight-line communication
multi-stage communication
computation between communications
predicate-controlled communication
values crossing communication boundaries
branch / merge behavior
parameterized widths
selected values
fork / join when supported
```

Validation should eventually include:

```text
source
→ compiler
→ generated SystemVerilog
→ elaboration
→ synthesis
→ simulation / behavioral checking
```

---

## 10. Regression Rule

For every compiler change:

```text
focused tests
    ↓
full pytest regression
```

Before committing:

```bash
git diff --check
pytest -q
```

A historical test should be reintroduced only when its semantic requirement is valid under the current architecture.
