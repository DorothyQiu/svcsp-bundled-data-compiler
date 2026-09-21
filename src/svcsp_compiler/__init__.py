"""SVCSP compiler frontend and Behavioral CSP IR."""

from .frontend import FrontendError, parse_file, parse_text
from .behavioral_ir import (
    Assign, BehavioralIRError, BehavioralModule, ChannelEndpoint, Expression, If,
    Parallel, Receive, Send, Sequence, Skip, SourceLocation, Variable, lower_behavioral,
)
from .communication_normalization import (
    DummyToken, Enable, NormalizationError, NormalizedModule, NormalizedReceive,
    NormalizedSend, normalize_communication,
)
from .dependency_analysis import (
    DependencyAnalysisError, DependencyEdge, DependencyGraph, DependencyKind,
    DependencyNode, NodeKind, analyze_dependencies,
)

__all__ = [
    "Assign", "BehavioralIRError", "BehavioralModule", "ChannelEndpoint", "Expression",
    "FrontendError", "If", "Parallel", "Receive", "Send", "Sequence", "Skip",
    "SourceLocation", "Variable",
    "DummyToken", "Enable", "NormalizationError", "NormalizedModule",
    "NormalizedReceive", "NormalizedSend", "lower_behavioral",
    "normalize_communication", "parse_file", "parse_text",
    "DependencyAnalysisError", "DependencyEdge", "DependencyGraph", "DependencyKind",
    "DependencyNode", "NodeKind", "analyze_dependencies",
]
