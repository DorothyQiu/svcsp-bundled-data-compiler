# SVCSP Bundled-Data Compiler

An automated compiler from a supported subset of **SystemVerilog CSP (SVCSP)**
to structural **bundled-data asynchronous RTL**.

The project explores a compiler-based path from behavioral CSP descriptions to
explicit asynchronous hardware structures while preserving communication,
control-flow, and blocking semantics through intermediate representations and
verification.

> **Current status:** the compiler implements the complete Phase 1–7B IR and
> code-generation pipeline. A linear `Receive; Assign*; Send` transaction is
> validated end to end in RTL, and direct single-site conditional Send/Receive
> decomposition is validated through original-vs-decomposed behavioral
> equivalence.

---

## Motivation

SVCSP provides a convenient behavioral notation for communicating concurrent
processes, but synthesizable bundled-data asynchronous hardware requires
explicit decisions about:

- communication structure;
- control and data dependencies;
- pipeline boundaries;
- handshake controllers;
- storage;
- matched-delay requirements; and
- structural RTL connectivity.

A direct syntax-to-RTL translation makes these architectural decisions difficult
to reason about and verify.

This project instead uses a staged compiler architecture:

```text
Behavioral SVCSP
      |
      v
Frontend / semantic extraction
      |
      v
Behavioral CSP IR
      |
      v
Communication normalization
      |
      v
Dependency analysis
      |
      v
Pipeline synthesis
      |
      v
Bundled-data microarchitecture IR
      |
      v
Template binding
      |
      v
Structural SystemVerilog
```

Conditional communication also has a separate verification path:

```text
                  Phase 3 NormalizedModule
                     /                \
                    /                  \
                   v                    v
        Phase 4-7 backend       Decomposed SVCSP
                   |             BODY + wrappers
                   v                    |
          Structural RTL               v
                              behavioral equivalence
                              with original SVCSP
```

The decomposed SVCSP is emitted from the **same Phase 3 IR**. It is not a
second compiler IR and is never reparsed into the backend.

---

## Compiler architecture

| Phase | Function |
| --- | --- |
| **1. Frontend** | Parse the supported SVCSP subset with `pyslang` and extract declarations, channels, control structure, widths, lexical identity, and source locations. |
| **2. Behavioral CSP IR** | Represent behavioral `Sequence`, `Parallel`, `If`, `Send`, `Receive`, `Assign`, and `Skip` independently of parser objects. |
| **3. Communication normalization** | Make conditional communication semantics explicit using communication sites, symbolic enables, BODY-side channels/communications, and Send/Receive wrapper semantics. |
| **4. Dependency analysis** | Build explicit DATA, SEQUENCE, CONTROL, COMMUNICATION, and PARALLEL_JOIN dependencies and validate conditional-receive data validity. |
| **5. Pipeline synthesis** | Form conservative pipeline stages while preserving communication boundaries, ordering, and wrapper attachment. |
| **6. Microarchitecture selection** | Select abstract BODY controllers, conditional wrappers, storage intent, and matched-delay intent. |
| **7A. Template binding** | Bind structural template contracts, ports, parameters, drivers, widths, and interfaces. |
| **7B. RTL emission** | Deterministically emit structural SystemVerilog without making new architectural decisions. |

Detailed compiler contracts and invariants are documented in [`docs/`](docs/).
[`AGENTS.md`](AGENTS.md) provides a compact architecture reference for
repository development.

---

## Conditional communication decomposition

Conditional communication is normalized before the bundled-data backend.

For example, a conditional Send:

```systemverilog
if (cond)
    R.Send(data);
```

is represented so that the BODY performs an unconditional internal token
transfer while a SEND wrapper controls whether external communication occurs.

The decomposed SVCSP preserves the original blocking semantics:

```text
BODY                    SEND wrapper

enable  -------------->
data    --------------> consume BODY token
                         |
                         +-- enabled --> external R.Send
                         |               completes
                         |
completion <------------+
   |
   v
BODY continuation
```

For a disabled Send, the wrapper suppresses external communication and returns
completion immediately.

For a conditional Receive, the wrapper leaves the external input untouched on
a disabled iteration while still providing a dummy/invalid internal token so
that BODY-side communication can progress.

### Verified conditional behavior

The current decomposed-SVCSP emitter supports one direct conditional
communication site per module:

- direct conditional Send;
- direct conditional Receive;
- blocking-order preservation after conditional Send.

Original and decomposed SVCSP are compared using real four-phase behavioral
simulation.

Current intentionally unsupported cases fail closed, including:

- multiple conditional communication sites in one emitted module;
- repeated conditional operations on the same external endpoint;
- nested conditional sites;
- conditional communication inside `fork`/`join`.

The Phase 3 IR can represent more structure than the current decomposed-SVCSP
emitter supports.

---

## End-to-end bundled-data RTL MVP

The current executable RTL path supports one unconditional linear transaction:

```systemverilog
A.Receive(a);
b = a + c;
B.Send(b);
```

The compiler maps this to one BODY stage:

```text
            data path
A.payload -----------> combinational BODY -----------> storage -----------> B.payload
                           (a + c)

            control path
A.req/ack -----------> linear controller -----------> matched delay ------> B.req
B.ack --------------------------------------------------------------------> controller
```

Receive and Send are the external communication boundaries of the stage; they
are not separate handshake stages.

For the current 8-bit regression fixture, the generated structure contains:

- one linear controller;
- one 8-bit output storage element;
- one matched-delay element.

The end-to-end simulation drives:

```text
a = 5
c = 3
```

and verifies:

```text
B.payload = 8
```

with the output payload stable before the downstream request is observed.

---

## RTL library

The current RTL library contains the minimal structures required for the linear
MVP:

```text
rtl_lib/
├── controllers/
│   └── four_phase_linear_controller.sv
├── storage/
│   └── transparent_latch.sv
└── delay/
    └── matched_delay.sv
```

Current limitations:

- the matched delay is a simulation-oriented delayed assignment, not a
  technology-characterized physical delay implementation;
- the controller and storage models currently use simulation/bootstrap
  initialization;
- JOIN and conditional wrapper structures are represented by the compiler but
  are not yet implemented as executable end-to-end RTL library components.

---

## Supported SVCSP subset

The frontend currently supports a deliberately conservative subset including:

- one module with one plain top-level `always` process;
- supported Channel/interface ports;
- module- and block-scoped `logic`, `reg`, and `bit` declarations;
- blocking assignments;
- `Send` and `Receive`;
- `begin` / `end`;
- `if` / `else`;
- `fork` / `join`;
- scalar, concrete packed, and supported symbolic packed widths;
- lexical variable identity and selected channel endpoints;
- conservative symbolic expressions.

The compiler fails closed when semantics cannot be proven.

In particular, it does not silently introduce:

- casts;
- truncation;
- extension;
- width promotion;
- implicit payload resizing;
- guessed default widths.

The executable RTL MVP is narrower than the frontend and IR support.

---

## Reproducing the current results

### Setup

Requirements:

- Python 3.10+
- `pyslang >= 11, < 12`
- `pytest >= 7`
- Icarus Verilog for simulator-backed tests

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
```

Check the tools:

```bash
python -c "import pyslang; print(pyslang.__version__)"

which iverilog
which vvp
iverilog -V
```

Icarus Verilog 12.0 is the currently tested simulator version.

### Run the complete regression suite

```bash
python -m pytest -q
```

Show tests skipped because a simulator is unavailable:

```bash
python -m pytest -q -rs
```

### Compile the linear RTL example

```python
from pathlib import Path
from svcsp_compiler import compile_linear_file

rtl = compile_linear_file(
    "tests/fixtures/linear_receive_add_send.sv"
)

Path("linear_receive_add_send.generated.sv").write_text(rtl)
```

Then compile the generated RTL with the current library:

```bash
iverilog -g2012 -s tb -o simulation \
  rtl_lib/controllers/four_phase_linear_controller.sv \
  rtl_lib/storage/transparent_latch.sv \
  rtl_lib/delay/matched_delay.sv \
  linear_receive_add_send.generated.sv \
  tb.sv

vvp simulation
```

---

## Current verification coverage

| Capability | Current status |
| --- | --- |
| Frontend and semantic extraction | Tested |
| Behavioral CSP IR | Tested |
| Conditional communication normalization | Tested |
| Dependency and validity analysis | Tested |
| Pipeline synthesis | Tested |
| Microarchitecture selection | Tested |
| Template binding | Tested |
| Structural RTL emission | Tested |
| Conditional Send decomposition | Behavioral equivalence tested |
| Conditional Receive decomposition | Behavioral equivalence tested |
| Conditional Send blocking-order preservation | Regression tested |
| Linear bundled-data RTL | End-to-end simulated |
| JOIN RTL | Not yet implemented |
| Conditional wrapper RTL | Not yet implemented |
| Physical matched-delay implementation | Not yet implemented |

The behavioral-equivalence tests use the same test-only mechanical lowering for
both original and decomposed Channel-based SVCSP when running under Icarus.
That lowering is a simulator compatibility mechanism, not compiler output.

---

## Repository structure

```text
src/svcsp_compiler/   compiler implementation
rtl_lib/              bundled-data RTL templates
tests/                compiler and simulation regressions
examples/             SVCSP examples
docs/                 compiler architecture and IR contracts
reference/            read-only reference implementations
```

---

## Roadmap

Near-term work focuses on extending validated compiler coverage rather than
adding code-generation shortcuts:

1. support multiple conditional communication sites while preserving blocking
   semantics and source ordering;
2. extend decomposed-SVCSP verification to more complex conditional structure;
3. implement and validate JOIN synchronization RTL;
4. implement conditional Send/Receive wrapper RTL;
5. add explicit shared-channel arbitration or mux architecture;
6. expand controller and storage template choices;
7. replace simulation-only matched delay with characterized or
   technology-specific implementations;
8. define a portable reset/bootstrap strategy;
9. broaden supported SVCSP syntax and concurrency/liveness verification.

---

## Design principle

The central design rule of the project is:

> **Architectural decisions belong in explicit compiler passes and IRs, not in
> RTL code generation.**

This separation is intended to make communication semantics, hardware
structure, and verification assumptions explicit and testable.