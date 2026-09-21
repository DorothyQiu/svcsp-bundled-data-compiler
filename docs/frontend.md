# Phase 1 frontend

```python
from svcsp_compiler import parse_file, parse_text, FrontendError

result = parse_file("examples/simple_buffer.sv")
print(result["module"])
print([(c["name"], c["direction"]) for c in result["channels"]])
```

`parse_text(source, filename="source.sv")` and `parse_file(path,
include_dirs=())` return JSON-compatible dictionaries. Both accept
`channel_types=("Channel",)` to configure named channel interfaces. Generic
`interface` ports are always treated as channels. Interface definitions are not
required: this is module-local source analysis, not SystemVerilog elaboration.

The result contains `module`, `location`, `variables`, `channels`, `operations`,
`always`, `syntax`, and parser `warnings`. Syntax dictionaries preserve pyslang
syntax kinds and fields, with token spellings and one-based source locations.
`always` retains if/else and fork/join nesting exactly as parsed. Each operation
has a method, channel, receiver, argument, location, and `syntax_path` relative
to the report. Variables retain their source type, dimensions, initializer,
location, lexical scope path, and JSON-compatible `payload_type` metadata. A
payload type records its `logic`/`reg`/`bit` base, concrete or symbolic packed
width, and source packed range. Scalars have width 1; `logic [7:0]` has width
8; and `logic [W-1:0]` retains the symbolic width identity `W` without
evaluating it. Symbolic packed widths require an owned supported module
parameter declaration, for example `module m #(parameter int W = 8)`. The
frontend retains each parameter's name, module ownership, source location, and
default expression text. No Behavioral CSP IR is introduced.

Named channel types may use one positional payload-width parameter, such as
`Channel #(W) C`, which is preserved symbolically with the same parameter
identity. Generic `interface` ports
do not declare a payload type in the supported syntax; later lowering may use a
declared Send value or Receive target type as that endpoint's payload context.

Directions aggregate all syntactic uses, without evaluating branch conditions:
Receive is input, Send is output, both is bidirectional, and no use is unknown.
Array endpoint directions aggregate all elements. A communication requires one
positional argument; Receive and blocking assignment targets must be declared
local variables (optionally selected). Calls on shadowed channels are rejected.

The supported subset has exactly one module and one plain top-level `always`,
ANSI channel/interface ports, module/block logic/reg/bit declarations, blocking
`=` assignments, expression if/else, begin/end, and fork/join. Empty blocks and
empty statements inside blocks are preserved. Custom channel types must be
configured explicitly. Only `parameter int` declarations used as symbolic
packed-width owners are supported; other parameter forms, ordinary signal ports,
hierarchy, tasks, loops, timing controls, nonblocking assignments, join_any/join_none, and other
unsupported statements produce `FrontendError` with a source location. Parser
errors also fail closed. File loading supports pyslang includes and macros.

Expression syntax and declared variable references are preserved and checked.
Packed widths are preserved but not evaluated beyond concrete integer ranges;
array bounds, definite assignment, concurrency races, and handshake behavior
are not elaborated or verified in this phase.
