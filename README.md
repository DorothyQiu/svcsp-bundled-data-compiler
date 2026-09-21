# SVCSP Bundled-Data Compiler

A Python compiler for a supported SystemVerilog CSP (SVCSP) subset. It preserves
behavioral communication semantics through explicit compiler passes and emits
structural SystemVerilog for the current linear bundled-data MVP.

The project goal is not a syntax-only translation. The compiler separates
behavioral CSP, normalized conditional communication, dependency analysis,
pipeline structure, microarchitecture selection, template binding, and RTL
emission so that architectural decisions are made before code generation.

## Current status

Phases 1 through 7B are implemented. Phase 1 uses `pyslang` to parse and
extract supported SystemVerilog semantics; Phase 2 onward uses compiler-owned,
syntax-independent IRs and metadata:

| Phase | Pass | Current capability |
| --- | --- | --- |
| 1 | Frontend and semantic extraction | Parses the supported SVCSP subset with `pyslang`; extracts declarations, channels, operations, lexical scope, widths, control structure, and source locations. |
| 2 | Behavioral CSP IR | Lowers to `Sequence`, `Parallel`, `If`, `Send`, `Receive`, `Assign`, and `Skip` without pyslang objects. |
| 3 | Communication normalization | Represents each conditional occurrence with `CommunicationSite`, symbolic `Enable`, explicit `BodyChannel` and unconditional `BodySend`/`BodyReceive`, plus normalized Send/Receive wrapper semantics. |
| 4 | Dependency analysis | Builds DATA, SEQUENCE, CONTROL, COMMUNICATION, and PARALLEL_JOIN edges, including conservative conditional-receive validity checks. |
| 5 | Pipeline synthesis | Forms pipeline stages and retains dependency, wrapper, and boundary metadata. The linear transaction form is grouped into one BODY stage. |
| 6 | Microarchitecture IR | Selects abstract LINEAR, JOIN, CONDITIONAL_SEND, and CONDITIONAL_RECV controller kinds, symbolic storage intent, and symbolic matched-delay intent. |
| 7A | Template binding | Binds explicit template contracts, port maps, parameter bindings, signal drivers, module ports, payload widths, and external interfaces. |
| 7B | Structural RTL emission | Mechanically emits deterministic SystemVerilog from the bound graph. It does not select controllers, storage, delays, widths, or wiring. |

The full IR pipeline supports more structure than the current executable RTL
library. JOIN and conditional communication are represented in compiler IRs,
but they are **not** supported by the current end-to-end bundled-data RTL MVP.

Conditional communication also has a Phase-3 verification/output branch. The
same `NormalizedModule` can emit decomposed Channel-based SVCSP containing BODY
plus SEND/RECV wrappers. This decomposed SVCSP is not a second IR and is never
reparsed into the compiler backend.

Direct single-site conditional Send and Receive decompositions are validated
against the original SVCSP by behavioral simulation. Conditional Send emission
uses a wrapper-to-BODY completion Channel so BODY continuation preserves the
blocking semantics of the original external `Send`.

## Linear end-to-end MVP

The current executable vertical slice accepts one unconditional linear
transaction:

```systemverilog
A.Receive(a);
b = a + c;
B.Send(b);
```

It forms one bundled-data BODY stage:

```text
A.payload ──> combinational BODY (a + c) ──> output storage ──> B.payload
A.req/A.ack ──> one linear controller ──> matched delay ──> B.req
B.ack ────────────────────────────────────────────────────────> controller
```

Receive and Send are external boundary metadata of that BODY stage; they are
not separate hardware stages and do not create internal handshakes. For the
8-bit fixture at `tests/fixtures/linear_receive_add_send.sv`, emitted RTL has
exactly:

- one `linear_controller`;
- one `abstract_storage #(.WIDTH(8))`; and
- one `symbolic_matched_delay`.

The end-to-end compiler entry point fails closed for fork/join, conditional
communication wrappers, multiple Receive or Send boundaries, and other
non-single-stage topologies.

The validated end-to-end case is the fixture's nontrivial combinational BODY
(`a + c`). Its testbench sets the fixture-local addend `c=3`, drives `a=5`, and
checks that the stored B payload is `8` before the B request is visible.
Direct or otherwise trivial forwarding is accepted by the linear compiler
shape, but still needs dedicated bundled-data timing validation.

## RTL library

`rtl_lib/` currently implements only the linear four-phase bundled-data MVP:

- `linear_controller` adapts the generated template name to
  `four_phase_linear_controller`.
- `abstract_storage` adapts to the parameterized `transparent_latch`.
- `symbolic_matched_delay` adapts to the simulation-oriented `matched_delay`.

`four_phase_linear_controller` implements a half-buffer C-element state over
`lreq` and `!rack`: it rises for `lreq=1, rack=0`, falls for `lreq=0, rack=1`,
and otherwise holds. Its `lack`, `latch_en`, and `raw_rreq` outputs equal that
state. It has no payload ports.

`transparent_latch` is an ordinary level-sensitive latch model: it is
transparent while enabled and retains its prior output while disabled.

`matched_delay` uses a delayed continuous assignment for simulation and has a
`1ns/1ps` timescale. Its `DELAY` parameter is therefore expressed in
nanoseconds in the current model. It is not a technology-mapped or
synthesizable physical delay line.

The controller and latch use simulation/bootstrap initialization in this MVP;
there is no portable ASIC reset architecture yet.

## Requirements and setup

- Python 3.10 or newer
- `pyslang >= 11, < 12`
- `pytest >= 7` for tests
- Icarus Verilog, providing both `iverilog` and `vvp`, for simulator-backed RTL
  tests; Icarus Verilog 12.0 is the tested version

Create an isolated environment and install the project with test dependencies:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
```

`pyslang` is a required project dependency and is installed by the editable
install above. Check the parser installation with:

```bash
python -c "import pyslang; print(pyslang.__version__)"
```

Install Icarus Verilog through your operating system's package manager or a
distribution appropriate for your platform, then verify both executables are
on `PATH`:

```bash
which iverilog
which vvp
iverilog -V
vvp -V
```

The simulator-backed tests skip only when either executable is unavailable. To
show those skips in the test summary, run `python -m pytest -q -rs`.

## Compile a linear source file

`compile_linear_file()` composes the existing passes in their normal order:
frontend, Behavioral IR, normalization, dependency analysis, pipeline
synthesis, microarchitecture selection, template binding, and RTL emission.

```python
from pathlib import Path
from svcsp_compiler import compile_linear_file

rtl = compile_linear_file("tests/fixtures/linear_receive_add_send.sv")
Path("linear_receive_add_send.generated.sv").write_text(rtl)
```

The returned text is structural SystemVerilog that references the selected
library template names. Compile it with the MVP library and a testbench using
Icarus, for example:

```bash
iverilog -g2012 -s tb -o simulation \
  rtl_lib/controllers/four_phase_linear_controller.sv \
  rtl_lib/storage/transparent_latch.sv \
  rtl_lib/delay/matched_delay.sv \
  linear_receive_add_send.generated.sv tb.sv
vvp simulation
```

The repository end-to-end test sets the fixture-local `c=3` from the testbench,
presents `A.payload=5`, completes the A request/acknowledge cycle, observes a
B request carrying `B.payload=8` with the payload already stable, and completes
the reciprocal B acknowledge cycle.

## Supported source subset

The frontend intentionally accepts a limited, fail-closed subset:

- one module with one plain top-level `always` process;
- ANSI channel/interface ports, including supported `Channel #(width)` payload
  declarations;
- local `logic`, `reg`, and `bit` declarations in module or block scope;
- scalar, concrete packed, and supported parameter-owned symbolic packed
  widths;
- blocking assignments, `Send`, `Receive`, `begin`/`end`, `if`/`else`, and
  `fork`/`join` in the compiler IR;
- lexical variable identity, selected channel endpoints, selected variable
  targets, symbolic expressions, and source locations.

The expression and width rules are deliberately conservative. Payload widths
must be independently provable and exactly compatible. The compiler does not
silently insert casts, resizes, truncation, extension, promotion, or default
literal sizing. Unsupported syntax, unsupported expression forms, unresolved
symbolic width ownership, and unprovable payload compatibility fail closed.

The executable linear MVP is narrower than the frontend and IR subset: it
requires exactly one unconditional `Receive; Assign*; Send` transaction with a
single Receive and Send boundary. The current end-to-end timing validation is
limited to the nontrivial `a + c` fixture described above.

## Tests

Run the complete suite:

```bash
python -m pytest -q
```

Run focused compiler-pass tests:

```bash
python -m pytest -q tests/test_frontend.py
python -m pytest -q tests/test_behavioral_ir.py
python -m pytest -q tests/test_communication_normalization.py
python -m pytest -q tests/test_dependency_analysis.py
python -m pytest -q tests/test_decomposed_svcsp.py
python -m pytest -q tests/test_pipeline_synthesis.py
python -m pytest -q tests/test_microarchitecture_ir.py
python -m pytest -q tests/test_template_binding.py
python -m pytest -q tests/test_rtl_codegen.py
```

Run width and end-to-end coverage:

```bash
python -m pytest -q tests/test_payload_widths.py tests/test_expression_widths.py
python -m pytest -q tests/test_end_to_end_linear.py
python -m pytest -q tests/test_rtl_library_mvp.py
```

The test categories cover frontend rejection and extraction, behavioral and
normalized IR semantics, dependency and validity guards, pipeline and
microarchitecture metadata, template contracts and driver ownership, payload
width propagation, mechanical RTL emission, RTL-library behavior, and the
linear end-to-end transaction. The last two categories use Icarus when it is
available.

## Implemented in IR vs. executable end to end

| Capability | Implemented in compiler IR | Supported by linear RTL MVP |
| --- | --- | --- |
| Unconditional linear Receive/Assign/Send | Yes | Validated only for the nontrivial `a + c` fixture |
| Direct/trivial forwarding timing | Grouped by the linear compiler | Not yet end-to-end timing validated |
| Exact concrete and parameter-owned symbolic payload widths | Yes | Yes |
| Mechanical structural SystemVerilog emission | Yes | Yes, for the selected linear templates |
| Conditional Receive/Send normalization | Yes; explicit site, enable, BODY channel, BODY communication, and wrapper identities | No |
| Decomposed SVCSP verification view | Yes; direct single-site Send/Receive behaviorally equivalence-tested | Separate verification output, not bundled-data RTL |
| Conditional communication wrapper templates | Bound and emitted structurally | No RTL-library implementation |
| Fork/join and JOIN topology | Represented and analyzed | No |
| Shared channels, arbitration, muxing | Rejected where ownership is ambiguous | No |
| Concrete storage primitive selection | No; abstract storage only | Transparent latch is the sole MVP adapter |
| Physical matched-delay implementation | No; symbolic intent only | Simulation-only delayed assignment model |

## Limitations and roadmap

Conditional communication decomposition is implemented for one direct
conditional communication site. Both conditional Send and conditional Receive
have original-vs-decomposed behavioral-equivalence tests.

For conditional Send, the emitted SEND wrapper returns a completion token only
after an enabled external blocking `Send` completes; the disabled path returns
completion without performing external communication. This preserves source
ordering for later BODY operations.

For conditional Receive, the disabled wrapper does not consume or acknowledge
the external channel but still sends a dummy/invalid BODY-side token so the
unconditional BODY Receive can progress.

Production decomposed SVCSP remains Channel-based. Icarus Verilog 12 cannot use
these Channel interfaces as useful module ports, so equivalence tests apply the
same test-only mechanical Channel-to-payload/request/acknowledge lowering to
both original and decomposed sources. This lowering is not compiler output.

Current decomposed-emitter limitations intentionally fail closed for multiple
conditional sites, repeated conditional operations on one endpoint, nested
conditional sites, and conditional communication inside fork/join.

The immediate roadmap is to extend the RTL library and verification only after
the corresponding bound-template contracts and microarchitecture decisions are
established. Planned work includes:

1. Extend decomposed-SVCSP emission beyond the current direct single-site
   support while preserving source ordering and blocking communication
   semantics.
2. JOIN controller and synchronization template implementation.
3. Conditional Send/Receive wrapper RTL templates and end-to-end validation.
4. Explicit shared-channel arbitration or mux architecture.
5. Additional controller and storage variants, selected in a prior compiler
   phase rather than by code generation.
6. Technology-specific or characterized bundled-data delay implementations.
7. A defined reset/bootstrap strategy suitable for the intended implementation
   technology.
8. Broader source-language support, elaboration, and verification of liveness,
   concurrency, and timing assumptions.

`reference/` is read-only design reference material. The compiler and MVP RTL
library are developed in `src/svcsp_compiler/` and `rtl_lib/`, respectively.
