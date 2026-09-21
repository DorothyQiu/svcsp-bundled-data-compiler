"""SVCSP compiler frontend and Behavioral CSP IR."""

from .frontend import FrontendError, parse_file, parse_text
from .behavioral_ir import (
    Assign, BehavioralIRError, BehavioralModule, ChannelEndpoint, Expression, If,
    Parameter, Parallel, PayloadType, PayloadWidth, Receive, Send, Sequence, Skip, SourceLocation, Variable,
    ONE_BIT, expression_payload_type, lower_behavioral, payload_types_compatible,
)
from .communication_normalization import (
    DummyToken, Enable, NormalizationError, NormalizedModule, NormalizedReceive,
    NormalizedSend, normalize_communication,
)
from .dependency_analysis import (
    DependencyAnalysisError, DependencyEdge, DependencyGraph, DependencyKind,
    DependencyNode, NodeKind, analyze_dependencies,
)
from .pipeline_synthesis import (
    PipelineDependency, PipelineGraph, PipelineMetadata, PipelineStage, PipelineSynthesisError,
    StageKind, WrapperAttachment, synthesize_pipeline,
)
from .microarchitecture_ir import (
    ControllerKind, HandshakePort, MatchedDelayRequirement, MicroarchitectureDependency,
    MicroarchitectureError, MicroarchitectureGraph, MicroarchitectureMetadata,
    MicroarchitectureStage, MicroarchitectureWrapper, StorageRequirement, select_microarchitecture,
)
from .template_binding import (
    BoundBodyStage, BoundLogicalSignal, BoundMatchedDelay, BoundPortBinding, BoundStorage,
    BoundTemplateParameterBinding,
    BoundModulePort, BoundStructuralDependency, BoundStructuralGraph, BoundWrapper, ModulePortRole,
    BoundSignalDriver, PortDirection, PortSemanticKind, SignalDriverKind,
    StructuralTemplate, TEMPLATE_CONTRACTS, TemplateBindingError, TemplateContract, TemplateParameter, TemplatePort,
    bind_templates, template_contract,
)
from .rtl_codegen import RTLCodegenError, emit_systemverilog

__all__ = [
    "Assign", "BehavioralIRError", "BehavioralModule", "ChannelEndpoint", "Expression",
    "FrontendError", "If", "Parameter", "Parallel", "PayloadType", "PayloadWidth", "Receive", "Send", "Sequence", "Skip",
    "SourceLocation", "Variable", "ONE_BIT", "expression_payload_type", "payload_types_compatible",
    "DummyToken", "Enable", "NormalizationError", "NormalizedModule",
    "NormalizedReceive", "NormalizedSend", "lower_behavioral",
    "normalize_communication", "parse_file", "parse_text",
    "DependencyAnalysisError", "DependencyEdge", "DependencyGraph", "DependencyKind",
    "DependencyNode", "NodeKind", "analyze_dependencies",
    "PipelineDependency", "PipelineGraph", "PipelineMetadata", "PipelineStage", "PipelineSynthesisError",
    "StageKind", "WrapperAttachment", "synthesize_pipeline",
    "ControllerKind", "HandshakePort", "MatchedDelayRequirement", "MicroarchitectureDependency",
    "MicroarchitectureError", "MicroarchitectureGraph", "MicroarchitectureMetadata",
    "MicroarchitectureStage", "MicroarchitectureWrapper", "StorageRequirement", "select_microarchitecture",
    "BoundBodyStage", "BoundLogicalSignal", "BoundMatchedDelay", "BoundPortBinding", "BoundStorage",
    "BoundTemplateParameterBinding",
    "BoundModulePort", "BoundStructuralDependency", "BoundStructuralGraph", "BoundWrapper", "ModulePortRole",
    "BoundSignalDriver", "PortDirection", "PortSemanticKind", "SignalDriverKind",
    "StructuralTemplate", "TEMPLATE_CONTRACTS", "TemplateBindingError", "TemplateContract", "TemplateParameter", "TemplatePort",
    "bind_templates", "template_contract",
    "RTLCodegenError", "emit_systemverilog",
]
