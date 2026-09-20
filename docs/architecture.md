# Compiler Architecture

## 1. Frontend
Parse SystemVerilog CSP and identify modules, channels, variables, Send/Receive, assignments, if/else, fork/join, control nesting, and source locations.

No RTL generation occurs here.

## 2. Behavioral CSP IR
Represent behavioral semantics using nodes such as:
- Sequence
- Parallel
- If
- Send
- Receive
- Assign

No controllers, pipeline stages, FFs, or delays exist at this level.

## 3. Communication Normalization
Transform conditional Send/Receive into BODY behavior plus enable signals and dedicated SEND/RECV wrapper representations.

Conditional Receive:
- enable=1: consume external token and forward real internal data
- enable=0: do not consume external token; generate dummy internal token

Conditional Send:
- consume BODY-side internal token
- enable=1: communicate externally
- enable=0: suppress external communication

## 4. Dependency Analysis
Build data, control, sequencing, communication, fork/join, and state dependencies.

## 5. Pipeline Synthesis
Partition normalized behavior into bundled-data pipeline stages.

Decide:
- stage boundaries
- storage placement
- combinational logic per stage
- fork/join structure
- controller kind

## 6. Controller / Template Selection
Initial controller types:
- linear
- join
- conditional send
- conditional receive

Future extensions may include conditional join, fork, call/arbiter, semi-decoupled, fully-decoupled, and Click controllers.

## 7. Bundled-Data Microarchitecture IR
Represent:
- pipeline stages
- controllers
- FF/latch storage
- combinational logic
- req/ack connections
- symbolic matched delays

## 8. RTL Code Generation
Generate structural synthesizable SystemVerilog from the microarchitecture IR.

Codegen must not decide architecture.

## 9. Verification
Verify:
- transformation correctness
- external communication behavior
- handshake correctness
- no unexpected token consumption
- deadlock/liveness behavior
- generated RTL syntax/simulation
