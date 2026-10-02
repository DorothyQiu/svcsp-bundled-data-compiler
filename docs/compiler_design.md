# SVCSP Compiler Design Specification

## 1. Status and Authority

This document is the normative architecture specification for the current SVCSP compiler design.

If this document conflicts with legacy implementation code or older compiler documentation, this document takes precedence.

Older documents may remain in the repository as historical or backend-specific references until they are explicitly reconciled with this specification.

The implementation must not preserve a legacy compiler abstraction solely because existing code depends on it.

---

## 2. Compiler Goal

The compiler translates the supported SVCSP subset of SystemVerilog into synthesizable asynchronous RTL.

The compiler separates:

1. source-language semantic understanding,
2. SVCSP semantic representation,
3. legality and dependency analysis,
4. asynchronous pipeline and state planning,
5. asynchronous microarchitecture construction,
6. template binding and RTL generation.

The compiler should reuse standard synchronous synthesis tools for datapath optimization whenever possible rather than reimplementing logic synthesis.

---

## 3. Target Compiler Flow

```text
SVCSP / SystemVerilog Source
        |
        v
1. pyslang Semantic Frontend
        |
        v
2. Unified Semantic Graph (USG)
        |
        v
3. Legality + Dependency Analysis
        |
        v
4. Pipeline + State Planning
        |
        v
5. Async Microarchitecture
        |
        v
6. Template Binding
        |
        v
7. Synthesizable SystemVerilog
        |
        v
8. External Synthesis / Validation
```

The key architectural boundary is:

```text
source semantics
        ->
USG
        ->
implementation planning
        ->
asynchronous hardware
```

Source parsing and graph construction must not make asynchronous hardware placement decisions.

---

## 4. Semantic Frontend

### 4.1 Parser

Use `pyslang` as the SystemVerilog semantic frontend.

The source must be parsed and semantically resolved once.

Later compiler stages must reuse the resolved semantic context rather than reparsing source text independently.

### 4.2 Responsibilities

The frontend resolves information required by later stages, including:

```text
modules
processes
Channel endpoints
variables
parameters
expressions
types
widths
lexical scope
Receive
Send
assignments
conditionals
source locations
semantic identities
```

### 4.3 Invariants

The frontend must:

- preserve source identities;
- preserve exact expression semantics;
- preserve widths and types;
- preserve Channel endpoint identity;
- preserve source locations when useful for diagnostics;
- fail closed when required semantics cannot be resolved.

The frontend must not:

- choose asynchronous stages;
- insert storage;
- select handshake templates;
- construct micropipeline topology;
- reinterpret source behavior to fit an existing backend.

---

## 5. Unified Semantic Graph

The Unified Semantic Graph (USG) is the compiler-owned semantic representation used after the pyslang frontend.

The USG contains only information needed for SVCSP compilation.

It is not intended to reproduce the complete SystemVerilog AST.

### 5.1 Core Node Kinds

The initial core node kinds are:

```text
Receive
Send
Assign
Predicate
```

#### Receive

Represents a source-level communication that receives a value from a Channel.

#### Send

Represents a source-level communication that sends a value through a Channel.

#### Assign

Represents a source-level computation or assignment.

#### Predicate

Represents a condition controlling execution or communication.

Examples include conditions originating from `if` statements.

### 5.2 Core Edge Kinds

The initial core edge kinds are:

```text
DATA
CONTROL
```

#### DATA

Represents semantic value dependence.

Example:

```systemverilog
A.Receive(a);
t = a + b;
B.Send(t);
```

may contain dependencies conceptually equivalent to:

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

#### CONTROL

Represents execution or communication controlled by a predicate.

Example:

```systemverilog
if (t)
    B.Send(x);
```

contains a CONTROL relation from the predicate to the Send operation.

Each CONTROL edge records whether it applies to the predicate's true or false
branch. This polarity describes source control semantics only; it does not imply
a particular control implementation.

### 5.3 USG Invariants

The USG must describe semantic dependencies, not physical hardware topology.

In particular:

- DATA edges do not imply pipeline-stage boundaries.
- CONTROL edges do not imply a specific mux, demux, gate, or controller.
- Graph construction must not insert asynchronous storage.
- Graph construction must not bind handshake templates.
- Communication source order must be preserved as semantic ordering information.
- `UnifiedSemanticGraph.nodes` preserves builder source or expanded semantic-occurrence order; this does not add an ORDER edge.
- Source semantic identity must remain recoverable from USG nodes.

One USG is built independently for each native `ProceduralBlockSymbol`.
Elaborated generate instances are discovered through the pyslang semantic
hierarchy; the compiler does not manually expand generate syntax. Generated
procedures are concurrent processes and have no cross-process semantic order.

---

## 6. Assignment and Value Semantics

Consider:

```systemverilog
x = x + a;
```

The compiler must not create an artificial Assign self-loop merely because `x` appears on both sides of the assignment.

The RHS reads the value of `x` available before the assignment.

The LHS defines the value of `x` available after the assignment.

Old/new value realization is handled by ordering, stage planning, and state planning rather than by introducing artificial cyclic DATA edges.

Therefore:

```text
x = x + a
```

does not imply:

```text
Assign(x) -> Assign(x)
```

in the USG.

The graph builder must preserve sufficient source ordering and definition/use information for later analysis to distinguish the consumed and produced values.

A future implementation may introduce explicit value-version objects if needed, but such a representation must preserve this semantic rule and must not be introduced merely to imitate SSA.

At a conditional join, definitions from each branch are retained as possible
reaching definitions. A later use has DATA dependencies on every such possible
definition. This does not introduce a MergeNode, SSA, or a value-version
representation.

---

## 7. Communication Ordering

Communication ordering is semantically significant.

For example:

```systemverilog
A.Receive(a);
B.Receive(b);
```

must not automatically be treated as two concurrent communications merely because no DATA edge exists between them.

Source communication order is separate from DATA dependence.

Communication order contributes to pipeline-stage planning.

The USG therefore needs to preserve both:

```text
value dependency
communication ordering
```

without forcing either relation to be represented as physical hardware during graph construction.

The exact internal representation of communication ordering may evolve, but the semantic ordering must not be lost.

---

## 8. Predicate Semantics

Predicates are represented explicitly in the USG.

Example:

```systemverilog
A.Receive(a);

if (a[0])
    C.Receive(a);

B.Send(a);
```

contains a Predicate depending on `a`.

Predicate placement is not fixed during graph construction.

The planner may place predicate computation in the same stage as the controlled communication or compute it earlier if its inputs are available earlier.

If a downstream stage needs a predicate derived from an upstream value, the predicate expression may be computed upstream and its result carried across a stage boundary.

The semantic condition must remain unchanged.

---

## 9. Legality and Structural Analysis

Legality checking is separate from USG construction.

The compiler should distinguish at least two concepts:

```text
source / language legality
backend realizability
```

### 9.1 Source / Language Legality

Reject constructs that are outside the supported synthesizable SVCSP subset.

Examples may include:

```text
#delay
simulation-only constructs
unsupported system tasks/functions
unbounded or non-static control structures
loops that cannot be statically resolved when required
unsupported dynamic language constructs
unresolvable semantic references
```

The compiler should reuse existing tools or synthesis checks where practical rather than attempting to implement a complete SystemVerilog synthesizability checker.

### 9.2 Backend Realizability

A source program may be synthesizable SystemVerilog but still unsupported by the current asynchronous backend.

Backend realizability therefore answers a different question:

```text
Can the current asynchronous microarchitecture and template library realize
this valid source behavior?
```

These two checks must not be conflated.

---

## 10. Dependency Analysis

Dependency analysis derives information used by the planner, including:

```text
data dependencies
control dependencies
communication ordering
definition/use relationships
value availability
value lifetime
```

Analysis must operate on semantic information from the frontend and USG.

It must not reinterpret source behavior in order to match legacy transaction or communication-decomposition abstractions.

---

## 11. Pipeline and State Planning

Pipeline and State Planning converts semantic dependencies into implementation decisions.

Its inputs include:

```text
communication ordering
DATA dependencies
CONTROL dependencies
value availability
value lifetime
backend capability
```

Its responsibilities include deciding:

```text
logical execution phases
operation and predicate placement within those phases
cross-phase value requirements
physical realization strategy
required storage and state lifetime
datapath-to-control relationships
```

This planning layer has two distinct concepts: Logical Execution Partitioning
and Realization Planning. A logical execution phase is not a physical
asynchronous pipeline stage.

### 11.1 Logical Execution Partitioning

Logical Execution Partitioning partitions source semantics into ordered,
path-aware execution phases. It preserves communication ordering and source
control semantics while identifying work that may execute as part of the same
logical phase.

A phase may contain zero or more Receive operations, RTL/control logic, and
zero or more Send operations. Independent parallel communications, and
conditional or mutually-exclusive communications, may remain in one phase when
their semantics permit it.

Communication ordering is a primary constraint on logical partitioning. DATA
dependence alone does not necessarily require a phase boundary.

Canonical examples:

```text
Receive -> Send
```

is one logical phase, as is:

```text
Receive -> comb -> Send
```

In contrast:

```text
Receive -> comb -> Send -> comb -> Send
```

is two logical phases. Whether and how those phases become physical stages is
a separate Realization Planning decision.

### 11.2 Cross-Phase Value Requirements

After logical partitioning, the planner derives cross-phase value requirements
from existing producer/consumer lifetime facts. If a value is produced in one
phase and consumed in a later phase, the realization plan must preserve it for
the required lifetime. This derivation must not reinterpret source semantics or
infer physical placement merely from a DATA edge.

### 11.3 Realization Planning

Realization Planning selects how a sequence of logical phases is realized.
The phases may be realized spatially as multiple physical pipeline stages, or
temporally through an explicit controller/state organization and stored values.
It makes the required storage, state lifetime, datapath-to-control
relationships, and physical stage boundaries explicit.

Feedback and recurrence are supported through explicit planned state; they do
not automatically require another physical pipeline stage. For example:

```systemverilog
x = x + a;
```

uses the earlier stored value of `x` and plans the later value as explicit
state. It is not rejected solely because it is a feedback recurrence.

### 11.4 Physical State Across Realized Boundaries

If Realization Planning places a required value on opposite sides of a physical
pipeline-stage boundary, it must arrange for the value to survive across that
boundary.

That may require physical state such as a register or equivalent stage storage.
Therefore the hardware between asynchronous control elements must not be
assumed to be purely combinational.

---

## 12. Stage-Local Datapath and State

The preferred hardware model is:

```text
Async Microarchitecture
|
+-- handshake / control structure
|
+-- stage-local datapath and state
    |
    +-- combinational logic
    |
    +-- required storage
```

This is intentionally more general than:

```text
async controllers + combinational islands
```

because some source semantics require values to persist across communication or pipeline boundaries.

The planner determines where such state is required.

The backend realizes that plan.

---

## 13. Async Microarchitecture

The Async Microarchitecture representation is the first compiler layer that describes explicit asynchronous hardware organization.

It should represent concepts such as:

```text
stages
stage-local datapath
stage-local state
handshake connections
branch / merge structure
control selection
storage resources
matched-delay requirements when applicable
```

This representation must be independent enough from a single RTL template implementation that alternative asynchronous template families can be explored.

The USG must not contain these hardware-specific decisions.

---

## 14. Template Binding

Template binding maps Async Microarchitecture objects onto concrete asynchronous building blocks.

Initial candidate templates include phase-decoupled Click-style structures such as:

```text
Source
Reg
Sink
Mux
Demux
```

Typical conceptual mapping may include:

```text
pipeline input       -> Source
linear pipeline      -> Reg
pipeline output      -> Sink
branch / selection   -> Mux / Demux
```

Selection inputs may be driven by predicate/control logic produced by the datapath.

This mapping is backend-specific and must not leak into USG construction.

Alternative asynchronous templates may be added later.

---

## 15. Datapath Synthesis

The compiler should avoid reimplementing conventional Boolean and arithmetic optimization.

Where possible:

```text
compiler
    -> organizes asynchronous stages, state, and control

standard synthesis tool
    -> optimizes stage-local datapath logic
```

Candidate downstream tools include commercial synthesis tools and open-source tools such as Yosys.

The generated RTL must preserve clear synthesis boundaries and synthesizable semantics.

---

## 16. RTL Generation

RTL generation consumes an already selected Async Microarchitecture and template binding.

It is responsible for:

```text
module generation
template instantiation
signal declaration
signal connectivity
datapath expression rendering
state realization
parameter propagation
width preservation
deterministic naming
```

RTL generation must not invent new semantic dependencies or silently change source behavior.

Architecture decisions belong to planning and microarchitecture construction, not to final text emission.

---

## 17. Synthesizability Strategy

Synthesizability is checked incrementally.

### Layer 1: Source Legality

Reject clearly unsupported or simulation-only source constructs.

### Layer 2: Backend Realizability

Verify that the planned semantics can be represented by the current asynchronous architecture.

### Layer 3: Generated RTL Validation

Use downstream tools to compile and synthesize generated SystemVerilog where possible.

This provides a final sanity check without requiring the compiler to implement a complete synthesis engine.

---

## 18. Example: Straight-Line Datapath

Source:

```systemverilog
A.Receive(a);
t = a + b;
B.Send(t);
```

Conceptual USG:

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

The USG does not determine physical register placement.

Pipeline and State Planning decides whether `t` is purely stage-local or must be stored across a stage boundary.

---

## 19. Example: Predicate-Controlled Communication

Source:

```systemverilog
A.Receive(a);

if (a[0])
    B.Send(a);
```

Conceptual USG:

```text
Receive(a)
   |
   | DATA
   v
Predicate(a[0])
   |
   | CONTROL
   v
Send(a)
```

There may also be a DATA relation from the received value to the Send payload.

The graph does not determine whether the backend realizes the branch using a Mux, Demux, gated stage, or another asynchronous structure.

That decision belongs to planning and template binding.

---

## 20. Example: Value Crossing a Communication Boundary

Source conceptually equivalent to:

```systemverilog
send(t);
t = a + b;

if (t)
    send(x);
```

The compiler must preserve the semantic lifetime of `t`.

If communication ordering creates a stage boundary before the later predicate uses `t`, Pipeline and State Planning must either:

- preserve `t` across that boundary, or
- place the required computation at an earlier valid point while preserving source semantics.

The USG itself must not fabricate a feedback dependency to represent this lifetime.

---

## 21. Non-Goals of the USG

The USG is not:

```text
a complete SystemVerilog AST
a netlist
an asynchronous controller graph
a physical timing graph
a template instance graph
an SSA requirement
```

It is a semantic graph used to bridge resolved SVCSP source semantics and later implementation planning.

---

## 22. Legacy Compiler Relationship

The existing compiler contains useful implementation assets, tests, frontend logic, RTL libraries, and previous asynchronous backend work.

These may be reused when consistent with this specification.

Legacy abstractions such as:

```text
Behavioral CSP IR
Transaction Extraction
Conditional Communication Decomposition
BODY / Enable / EN_RECV / EN_SEND
fixed M1-M7 phase ownership
```

are not automatically part of the new architecture.

They must be retained only if independently justified by the current design.

Legacy code should initially remain available as:

```text
implementation reference
behavioral reference
regression reference
test infrastructure
RTL-library reference
```

rather than being modified in place to force compatibility with the new flow.

---

## 23. Implementation Strategy

The new architecture should be implemented incrementally.

Recommended order:

```text
1. inspect and isolate reusable frontend functionality
2. define minimal USG data model
3. implement Receive / Send / Assign graph construction
4. add Predicate and CONTROL construction
5. implement legality checks
6. implement dependency and ordering analysis
7. implement Pipeline and State Planning
8. define Async Microarchitecture representation
9. implement template binding
10. emit and validate synthesizable RTL
```

Each step should have focused tests.

Avoid broad refactoring of legacy code until the replacement architecture has a working vertical path.

---

## 24. Design Rules for Implementation Agents

When implementing this specification:

- treat this document as authoritative;
- do not infer architecture from existing implementation code;
- do not introduce a new IR abstraction without a clear need;
- do not preserve legacy abstractions solely to minimize code changes;
- reuse existing code only when its semantics match this specification;
- make small independently testable changes;
- report conflicts between existing implementation and this specification;
- prefer explicit failure over silently changing semantics.

---

## 25. Open Design Questions

The following details remain intentionally open and should be resolved before their implementation becomes an architectural dependency:

1. Exact internal representation of communication ordering.
2. Whether explicit value-version objects are needed in the USG.
3. Exact stage-planning algorithm.
4. Exact representation of stage-local persistent state.
5. Exact Async Microarchitecture IR schema.
6. Selection of the initial concrete handshake template family.
7. Exact handling of fork/join semantics in the USG and planner.
8. Exact matched-delay representation and technology-binding boundary.

These are open design questions, not permission to fall back to legacy architecture implicitly.
