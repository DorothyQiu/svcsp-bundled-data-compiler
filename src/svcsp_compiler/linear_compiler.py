"""One fail-closed entry point for the supported linear MVP compiler slice."""
from __future__ import annotations

from pathlib import Path

from . import behavioral_ir as behavioral
from . import communication_normalization as normalization
from . import dependency_analysis as dependency
from . import microarchitecture_ir as microarchitecture
from . import pipeline_synthesis as pipeline
from . import template_binding
from .frontend import parse_file
from .behavioral_ir import lower_behavioral
from .communication_normalization import normalize_communication
from .dependency_analysis import analyze_dependencies
from .microarchitecture_ir import select_microarchitecture
from .pipeline_synthesis import synthesize_pipeline
from .rtl_codegen import emit_systemverilog
from .template_binding import bind_templates


class LinearCompilationError(ValueError):
    """The selected source cannot use the linear MVP template library."""


def _contains_parallel(process: behavioral.Process) -> bool:
    if isinstance(process, behavioral.Parallel):
        return True
    if isinstance(process, behavioral.Sequence):
        return any(_contains_parallel(item) for item in process.items)
    if isinstance(process, behavioral.If):
        return _contains_parallel(process.then_branch) or _contains_parallel(process.else_branch)
    return False


def _require_linear(behavioral_module: behavioral.BehavioralModule,
                    normalized: normalization.NormalizedModule,
                    dependency_graph: dependency.DependencyGraph,
                    pipeline_graph: pipeline.PipelineGraph,
                    microarchitecture_graph: microarchitecture.MicroarchitectureGraph) -> None:
    if _contains_parallel(behavioral_module.body):
        raise LinearCompilationError('linear MVP does not support fork/join')
    if normalized.wrappers or normalized.enables:
        raise LinearCompilationError('linear MVP does not support conditional communication wrappers')
    if any(node.kind is dependency.NodeKind.PARALLEL_JOIN for node in dependency_graph.nodes):
        raise LinearCompilationError('linear MVP does not support join synchronization')
    if pipeline_graph.attachments or any(stage.kind is not pipeline.StageKind.OPERATION
                                         for stage in pipeline_graph.stages):
        raise LinearCompilationError('linear MVP requires operation-only pipeline stages')
    if (len(pipeline_graph.stages) != 1 or pipeline_graph.metadata or
            pipeline_graph.stages[0].upstream_boundary is None or
            pipeline_graph.stages[0].downstream_boundary is None):
        raise LinearCompilationError(
            'linear MVP requires exactly one unconditional Receive; Assign*; Send BODY stage')
    if microarchitecture_graph.wrappers:
        raise LinearCompilationError('linear MVP does not support communication wrappers')
    if any(len(stage.handshake_inputs) > 1 or len(stage.handshake_outputs) > 1
           for stage in microarchitecture_graph.stages):
        raise LinearCompilationError('linear MVP requires a single-predecessor/single-successor topology')


def compile_linear_file(path: str | Path, *, include_dirs: tuple[str | Path, ...] = ()) -> str:
    """Compile one supported SVCSP file through all existing linear MVP passes."""
    frontend = parse_file(path, include_dirs=include_dirs)
    behavioral_module = lower_behavioral(frontend)
    normalized = normalize_communication(behavioral_module)
    dependency_graph = analyze_dependencies(normalized)
    pipeline_graph = synthesize_pipeline(dependency_graph)
    microarchitecture_graph = select_microarchitecture(pipeline_graph)
    _require_linear(behavioral_module, normalized, dependency_graph, pipeline_graph, microarchitecture_graph)
    bound = bind_templates(microarchitecture_graph)
    return emit_systemverilog(bound)
