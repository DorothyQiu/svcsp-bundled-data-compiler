"""Target SVCSP-to-bundled-data asynchronous RTL compiler APIs."""

from .async_compiler import compile_async_file
from .async_microarchitecture import (
    AckJoin, AsyncMicroarchitecture, BaseHalfBufferController, BufferStyle, CombinationalBlock, EnableAvailability,
    EnableChannel, EnableTokenProducer, EnReceiveStage, EnSendStage,
    HandshakeProtocol, InputAckDirectConnection, InputAckFanout,
    InputPort, InputRequestDirectConnection, MatchedDelayRequirement,
    OutputAckDirectConnection, OutputPort, OutputRequestDirectConnection,
    RequestFanout, RequestJoin, StageStorage, StorageSlot, TimingModel,
    lower_microarchitecture,
)
from .async_rtl_codegen import AsyncRTLCodegenError, emit_async_systemverilog
from .async_template_binding import (
    AsyncTemplateBindingError, BoundAsyncAssignment, BoundAsyncConnection,
    BoundAsyncInstance, BoundAsyncModule, BoundAsyncModulePort,
    BoundAsyncParameterBinding, BoundAsyncPortBinding, BoundAsyncSignal,
    BoundAsyncVariableBinding, BoundEnableChannel, bind_async_templates,
)
from .behavioral_ir import (
    Assign, BehavioralIRError, BehavioralModule, ChannelEndpoint, Expression, If,
    ONE_BIT, Parallel, Parameter, PayloadType, PayloadWidth, Receive, Send,
    Sequence, Skip, SourceLocation, Variable, expression_payload_type,
    lower_behavioral, payload_types_compatible,
)
from .communication_decomposition import (
    BodyReceive, BodySend, DecomposedTransaction, Enable, EnReceive, EnSend,
    InvalidPayload, decompose_transaction,
)
from .decomposed_svcsp import DecomposedSVCSPError, emit_conditional_send_decomposition
from .frontend import FrontendError, parse_file, parse_text
from .semantic_analysis import (
    ReceiveValidity, SemanticDependency, SemanticDependencyKind,
    SemanticValidationError, SemanticallyValidatedTransaction, analyze_semantics,
)
from .transaction import (
    RegionOperation, StructurallyValidatedTransaction, TransactionStructureError,
    TransactionWarning, extract_transaction,
)

__all__ = [
    "AckJoin", "Assign", "AsyncMicroarchitecture", "AsyncRTLCodegenError", "AsyncTemplateBindingError",
    "BehavioralIRError", "BehavioralModule", "BodyReceive", "BodySend", "BoundAsyncAssignment",
    "BoundAsyncConnection", "BoundAsyncInstance", "BoundAsyncModule", "BoundAsyncModulePort",
    "BoundAsyncParameterBinding", "BoundAsyncPortBinding", "BoundAsyncSignal", "BoundAsyncVariableBinding",
    "BaseHalfBufferController", "BoundEnableChannel", "BufferStyle", "ChannelEndpoint", "CombinationalBlock", "DecomposedSVCSPError",
    "DecomposedTransaction", "Enable", "EnableAvailability", "EnableChannel", "EnableTokenProducer",
    "EnReceive", "EnReceiveStage", "EnSend", "EnSendStage", "Expression", "FrontendError",
    "HandshakeProtocol", "If", "InputAckDirectConnection", "InputAckFanout", "InputPort",
    "InputRequestDirectConnection", "InvalidPayload", "MatchedDelayRequirement", "ONE_BIT",
    "OutputAckDirectConnection", "OutputPort", "OutputRequestDirectConnection", "Parallel", "Parameter",
    "PayloadType", "PayloadWidth",
    "Receive", "ReceiveValidity", "RegionOperation", "SemanticDependency", "SemanticDependencyKind",
    "SemanticValidationError", "SemanticallyValidatedTransaction", "Send", "Sequence", "Skip",
    "RequestFanout", "RequestJoin", "SourceLocation", "StageStorage", "StorageSlot",
    "StructurallyValidatedTransaction", "TimingModel",
    "TransactionStructureError", "TransactionWarning", "Variable", "analyze_semantics",
    "bind_async_templates", "compile_async_file", "decompose_transaction",
    "emit_async_systemverilog", "emit_conditional_send_decomposition", "expression_payload_type",
    "extract_transaction", "lower_behavioral", "lower_microarchitecture", "parse_file", "parse_text",
    "payload_types_compatible",
]
