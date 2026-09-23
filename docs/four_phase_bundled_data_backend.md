# Four-Phase Bundled-Data Backend

This document is the authoritative hardware specification for the initial
four-phase bundled-data backend.

Source-language support is defined in `supported_architectures.md`.
Conditional communication semantics are defined in
`communication_decomposition.md`.

## 1. Ordinary BODY Stage

An ordinary BODY transaction has:

```text
N independent input Channels
        |
        v
combinational logic
        |
        v
M independent output Channels
```

with `N >= 1` and `M >= 1`.

The target control structure is:

```text
Lreq[0..N-1]
      |
      v
Request Join
      |
      v
 base_Lreq
      |
      v
+-----------------------+
| Base 1x1 Half-Buffer  |
| Controller            |
+-----------------------+
   |                ^
   |                |
base_Lack        base_Rack
   |                |
   v                |
ACK Fanout       ACK Join
   |                ^
   v                |
Lack[0..N-1]   Rack[0..M-1]


Base controller q
      |
      v
Request Fanout
      |
      v
raw_req[0..M-1]
      |
      v
per-output matched delays
      |
      v
Rreq[0..M-1]
```

The bundled datapath is:

```text
Ldata[0..N-1]
      |
      v
combinational logic
      |
      v
structural output latch banks
      |
      v
Rdata[0..M-1]
```

The control and datapath structures are distinct but timing-related through
the matched-delay requirements.

## 2. Base Half-Buffer Controller

Every ordinary BODY stage contains exactly one canonical 1x1 half-buffer
controller.

Its state is:

```text
q = C(base_Lreq, !base_Rack)
```

where `C` is a Muller C-element.

The controller drives:

```text
base_Lack      = q
base_raw_Rreq  = q
storage_enable = q
```

The ordinary controller must be structural: a C-element primitive plus simple
gates/wires. It must not be implemented as a behavioral FSM.

### Control reset

The ordinary control state has an explicit active-low reset:

```text
reset_n = 0 -> q = 0
reset_n = 1 -> normal C-element behavior
```

A normal four-phase idle state has:

```text
base_Lreq = 0
base_Rack = 0
```

so the C-element inputs are:

```text
0
!0 = 1
```

which is a hold condition. Reset therefore establishes the required initial
controller state rather than relying on simulation initialization.

Reset applies to control state, not to the payload datapath.

### 1x1 bypass case

For `N = 1`, the input request join is bypassed.

For `M = 1`, the output acknowledgement join is bypassed.

Therefore a `1R1S` ordinary stage reduces to:

```text
Lreq ----------\
                > Base Half-Buffer CTRL -----> Lack
Rack ----------/           |
                            +----> storage_enable
                            |
                            +----> matched delay ----> Rreq

Ldata -> combinational logic -> latch bank -> Rdata
```

These bypasses are M6 architecture decisions, not M7 optimizations.

## 3. Input Request Join

For `N > 1`, input requests are combined before driving `base_Lreq`.

The join preserves Muller C-element semantics:

```text
all requests high -> output high
all requests low  -> output low
mixed values      -> retain state
```

For `N = 2`, the generic RTL library uses one two-input Muller C-element.

For `N > 2`, the generic RTL library may use a structural reduction network of
two-input Muller C-elements under the backend's four-phase monotonicity
assumption:

```text
assertion phase:
    each request transitions only 0 -> 1
    once high, it remains high until the phase completes

return-to-zero phase:
    each request transitions only 1 -> 0
    once low, it remains low until the phase completes
```

The generic reduction network is not claimed to be a universally safe
decomposition for arbitrary asynchronous input behavior.

Technology mapping may later replace the generic structure with dedicated
multi-input C-element cells.

## 4. Input Acknowledge Fanout

`base_Lack` is returned to all participating input Channels:

```text
base_Lack
   |
   +--> Lack[0]
   +--> Lack[1]
   ...
   +--> Lack[N-1]
```

This is structural wiring or buffering.

For `N = 1`, the connection is direct.

## 5. Output Request Fanout

The base controller produces one raw output request:

```text
base_raw_Rreq
```

For `M > 1`, it is structurally fanned out:

```text
                    +--> raw_req[0]
base_raw_Rreq ------+--> raw_req[1]
                    ...
                    +--> raw_req[M-1]
```

For `M = 1`, the connection is direct.

Each ordinary output branch then passes through its own matched delay.

## 6. Output Acknowledge Join

For `M > 1`, output acknowledgements are combined before driving `base_Rack`.

The join preserves Muller C-element semantics:

```text
all acknowledgements high -> output high
all acknowledgements low  -> output low
mixed values               -> retain state
```

For `M = 2`, the generic RTL library uses one two-input Muller C-element.

For `M > 2`, the same structural C-element reduction and monotonic four-phase
assumption used by the input request join applies.

For `M = 1`, the acknowledgement join is bypassed:

```text
base_Rack = Rack[0]
```

## 7. Payload Storage

Ordinary BODY payload storage is a structural level-sensitive latch bank.

For each output payload:

```text
combinational result
       |
       v
structural latch bank
       |
       v
output payload
```

The latch enable comes directly from the base-controller state:

```text
storage_enable = q
```

For payload width `W`, the generic RTL library may instantiate:

```text
W x latch_cell
```

The storage block must not independently invent handshake state.

Payload latch contents are not required to have a defined power-up value.

Correctness requires the delayed output request to become observable only
after the corresponding valid payload has propagated through the datapath and
latch bank, with the required timing margin.

The base controller may launch `base_raw_Rreq` together with
`storage_enable`; the per-output matched delay enforces the bundled-data timing
relationship before `Rreq` reaches the external Channel.

Payload latches therefore do not require simulation-only initialization or
architectural reset.

Technology mapping may replace the generic latch-cell primitive with a
standard-cell or custom latch implementation.

## 8. Matched Delay

Each ordinary BODY output has one matched-delay requirement:

```text
raw_req[i]
    |
    v
matched_delay[i]
    |
    v
Rreq[i]
```

The delay must conservatively match the worst-case bundled-data path associated
with that output plus the required timing margin.

Different output branches may require different delays.

The compiler decides:

```text
delay placement
associated datapath
output branch identity
```

The compiler does not select a final physical inverter count or delay-cell
implementation.

The current RTL library may use a simulation-oriented delay model.

A future technology binding may use:

```text
characterized delay cells
programmable delay chains
coarse/fine delay selection
slow/fast modes
post-silicon trim bits
technology-specific delay macros
```

Post-silicon delay programmability is a physical implementation concern rather
than an RTL inference performed by the compiler.

## 9. Implementation Boundary

The target implementation level is:

```text
Component                         Target representation
--------------------------------  --------------------------------
Base half-buffer controller       structural C-element/gates
Input request join                structural C-element logic
Input ACK fanout                  structural wiring/buffers
Output request fanout             structural wiring/buffers
Output ACK join                   structural C-element logic
Ordinary payload storage          structural latch bank
User combinational logic          behavioral/continuous RTL
EN_RECV controller                behavioral for now
EN_SEND controller                behavioral for now
Matched delay                     technology-binding abstraction
```

C-elements and latch cells may remain explicit RTL-library primitive
boundaries until technology mapping supplies their final implementations.

Reset is part of the ordinary control implementation and is not part of the
user payload datapath.

## 10. EN_RECV and EN_SEND Stages

Conditional communication is implemented using separate EN_RECV and EN_SEND
micropipeline stages, not conditional-split stages.

Each EN stage remains a physical micropipeline stage with:

```text
controller
+
structural payload storage
+
matched delay where an outgoing bundled-data request is launched
+
four-phase Channel interfaces
```

The controller differs from the ordinary base half-buffer controller because
it must also consume and interpret an enable token.

For the current backend, EN_RECV and EN_SEND controllers may remain behavioral
until their final control circuits are defined.

### EN_RECV

```text
enable = 1:
    consume enable
    perform external Receive
    store real payload
    launch BODY communication

enable = 0:
    consume enable
    leave external Channel untouched
    store InvalidPayload/dummy payload
    launch BODY communication
```

### EN_SEND

```text
always:
    consume enable
    consume BODY payload

enable = 1:
    store payload
    launch external Send

enable = 0:
    suppress external communication
```

Detailed enable-token semantics remain defined in
`communication_decomposition.md`.

Lecture-style conditional split stages are outside the current backend.

## 11. RTL Library Contract

The RTL library should expose architecture-level components such as:

```text
muller_c_element2
four_phase_half_buffer_controller
four_phase_request_join
four_phase_ack_fanout
four_phase_request_fanout
four_phase_ack_join
latch_cell
bundled_data_latch_bank
matched_delay
en_receive_controller
en_send_controller
```

Exact filenames and primitive decomposition may evolve, but the documented
architectural roles must remain explicit.

## 12. M6 / M7 Contract

M6 must determine:

```text
input request join or N=1 direct connection
one base half-buffer controller
input ACK fanout or direct connection
output request fanout or direct connection
output ACK join or M=1 direct connection
storage placement
per-output matched-delay placement
EN_RECV / EN_SEND placement
```

M7 only binds and emits the M6-selected structure.

M7 must not independently:

```text
insert or remove joins
insert or remove fanouts
change the number of base controllers
change storage control
change acknowledgement aggregation
move matched delays
replace EN_RECV / EN_SEND architecture
```

Generated hardware must remain structurally traceable to this backend
specification.

## 13. Physical-Implementation Boundary

The compiler produces explicit asynchronous microarchitecture, but the
following remain technology-specific:

```text
final C-element cell implementation
final latch-cell implementation
matched-delay cell realization
post-silicon delay programming
physical timing characterization
```

These belong to later technology mapping and physical-design stages.
