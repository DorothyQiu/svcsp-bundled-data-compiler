# Structural SystemVerilog emission

Phase 7B consumes a `BoundStructuralGraph` through
`emit_systemverilog(graph)` and returns deterministic structural SystemVerilog
text. It renders exactly the Phase 7A templates, logical signals, indexed port
bindings, parameter declarations, storage bindings, and matched-delay bindings
already present in the graph. It does not modify the graph or select an
architecture.

`BoundLogicalSignal` widths determine declarations directly: one-bit signals
are scalar `logic`, concrete payload widths are `logic [N-1:0]`, and symbolic
payload widths retain their declared packed range and owned module parameter.
The emitter declares only the module parameters carried through the bound graph.
It never evaluates a parameter or adds a conversion.

Each selected template emits one named instance: `linear_controller`,
`join_controller`, `conditional_recv_wrapper`, `conditional_send_wrapper`,
`abstract_storage`, or `symbolic_matched_delay`. BODY-stage instances are
emitted independently of wrapper instances. Storage and matched-delay instances
exist only when their corresponding Phase 7A bound objects exist.

Template parameters also come only from Phase 7A. Codegen groups the generic
graph-level `BoundTemplateParameterBinding` objects by their already-bound
instance and emits each exact formal/value pair. It never derives a value from
connected signals or relies on a library default. For the MVP, this emits the
required `abstract_storage` `.WIDTH(...)` binding.

The module header and declarations come from Phase 7A `BoundModulePort`
objects, separately from internal `BoundLogicalSignal` declarations. Receive
endpoint request and payload ports are inputs and its acknowledge port is an
output. Send endpoint request and payload ports are outputs and its acknowledge
port is an input. A module-port signal cannot be declared again as an internal
net. Conditional wrapper external ports bind to these same top-level signals;
BODY-side wrapper signals remain internal.

The emitter follows every `TemplateContract` and `BoundPortBinding` directly.
Every emitted connection uses the exact Phase 7A
`BoundPortBinding.formal_name`: `.formal_name(signal)`. For example, JOIN
fan-in member names are resolved by the Phase 7A port-family contract before
code generation. Phase 7B neither constructs indexed formal names nor adds a
connection or changes fan-in topology.

Names are mapped to legal SystemVerilog identifiers by deterministic namespace
prefixes and sanitization. Variables include their declaration identity in the
mapped name, which keeps lexical shadows distinct. Signals and instances derive
their names from their bound identities. The emitter rejects an identifier that
cannot be mapped collision-safely.

Only the existing symbolic expression subset is rendered: declared variable or
parameter references, packed selections, supported unary and binary operators,
conditionals, concatenations, replications, and parser-preserved literals. It
does not apply SystemVerilog sizing, promotion, casts, sign/zero extension,
truncation, or any other conversion.

Phase 7B renders continuous assignments only for logical signals whose Phase
7A `BoundSignalDriver` is a symbolic expression. It renders template output
connections only when the binding names that template output as the signal's
unique driver. BODY controller templates have no payload ports; payload drivers
are symbolic datapath signals, abstract-storage outputs, wrapper outputs, or
module inputs. The emitter rejects a pre-existing multiple-driver conflict and
does not synthesize arbitration, muxing, tri-state logic, or driver resolution.

Emission fails closed for missing required port bindings, unknown instances or
signals, incompatible port kinds, missing or inconsistent payload widths,
unresolved symbolic parameter ownership, invalid identifiers, and unsupported
expressions. The emitted module references the selected template module names;
template implementations remain outside this compiler pass.
