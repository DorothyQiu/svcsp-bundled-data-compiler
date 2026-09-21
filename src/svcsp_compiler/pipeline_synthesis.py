"""Phase 5: conservative behavioral pipeline partitioning."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import behavioral_ir as behavioral
from . import communication_normalization as normalization
from . import dependency_analysis as dependency


class StageKind(str, Enum):
    OPERATION = 'operation'
    JOIN = 'join'


@dataclass(frozen=True)
class PipelineStage:
    """One conservative stage, extensible to multiple operations later."""

    id: str
    kind: StageKind
    operations: tuple[dependency.DependencyNode, ...]
    endpoint: behavioral.ChannelEndpoint | None = None
    variable: behavioral.Variable | None = None
    location: behavioral.SourceLocation | None = None


@dataclass(frozen=True)
class PipelineDependency:
    """A Phase 4 edge retained as an inter-stage edge or metadata constraint."""

    source_node: str
    target_node: str
    kind: dependency.DependencyKind
    source_stage: str | None = None
    target_stage: str | None = None


@dataclass(frozen=True)
class WrapperAttachment:
    """A normalized external wrapper attached to its BODY-side stage."""

    wrapper_node: str
    body_stage: str
    endpoint: behavioral.ChannelEndpoint
    enable: normalization.Enable
    direction: str
    location: behavioral.SourceLocation | None = None


@dataclass(frozen=True)
class PipelineMetadata:
    """A non-datapath Phase 4 node retained as a pipeline constraint source."""

    id: str
    kind: dependency.NodeKind
    operation: object | None = None
    enable: normalization.Enable | None = None
    location: behavioral.SourceLocation | None = None


@dataclass(frozen=True)
class PipelineGraph:
    module: str
    stages: tuple[PipelineStage, ...]
    metadata: tuple[PipelineMetadata, ...]
    dependencies: tuple[PipelineDependency, ...]
    attachments: tuple[WrapperAttachment, ...]
    parameters: tuple[behavioral.Parameter, ...] = ()

    def stage_for(self, dependency_node: str) -> PipelineStage | None:
        for stage in self.stages:
            if any(operation.id == dependency_node for operation in stage.operations):
                return stage
        return None


class PipelineSynthesisError(ValueError):
    """A dependency graph cannot be partitioned by the conservative policy."""


def _stage_kind(node: dependency.DependencyNode) -> StageKind:
    if node.kind is dependency.NodeKind.OPERATION:
        return StageKind.OPERATION
    if node.kind is dependency.NodeKind.PARALLEL_JOIN:
        return StageKind.JOIN
    raise PipelineSynthesisError(f'node {node.id} is not stage-executable')


def _attachment(graph: dependency.DependencyGraph, wrapper: dependency.DependencyNode,
                stage_ids: dict[str, str], dependencies: tuple[dependency.DependencyEdge, ...]) -> WrapperAttachment:
    operation = wrapper.operation
    if not isinstance(operation, (normalization.NormalizedReceive, normalization.NormalizedSend)):
        raise PipelineSynthesisError(f'wrapper node {wrapper.id} has no normalized communication object')
    body_nodes = []
    for edge in dependencies:
        if edge.kind is not dependency.DependencyKind.COMMUNICATION:
            continue
        if edge.source == wrapper.id and edge.target in stage_ids:
            body_nodes.append(edge.target)
        if edge.target == wrapper.id and edge.source in stage_ids:
            body_nodes.append(edge.source)
    if len(body_nodes) != 1:
        raise PipelineSynthesisError(f'wrapper node {wrapper.id} must have exactly one BODY-side stage')
    return WrapperAttachment(
        wrapper_node=wrapper.id,
        body_stage=stage_ids[body_nodes[0]],
        endpoint=operation.endpoint,
        enable=operation.enable,
        direction='into_body' if isinstance(operation, normalization.NormalizedReceive) else 'from_body',
        location=operation.location,
    )


def synthesize_pipeline(graph: dependency.DependencyGraph) -> PipelineGraph:
    """Partition each executable dependency node into one conservative stage."""
    if not isinstance(graph, dependency.DependencyGraph):
        raise PipelineSynthesisError('expected a DependencyGraph')
    known_nodes = {node.id for node in graph.nodes}
    if len(known_nodes) != len(graph.nodes):
        raise PipelineSynthesisError('dependency graph has duplicate node identities')
    if any(edge.source not in known_nodes or edge.target not in known_nodes for edge in graph.edges):
        raise PipelineSynthesisError('dependency edge refers to an unknown node')

    stages: list[PipelineStage] = []
    stage_ids: dict[str, str] = {}
    for node in graph.nodes:
        if node.kind not in {dependency.NodeKind.OPERATION, dependency.NodeKind.PARALLEL_JOIN}:
            continue
        stage_id = f'stage_{len(stages)}'
        stage_ids[node.id] = stage_id
        stages.append(PipelineStage(stage_id, _stage_kind(node), (node,), node.endpoint,
                                    node.variable, node.location))
    metadata_nodes = {dependency.NodeKind.CONTROL, dependency.NodeKind.ENABLE, dependency.NodeKind.WRAPPER}
    if any(node.kind not in metadata_nodes and node.id not in stage_ids for node in graph.nodes):
        raise PipelineSynthesisError('dependency graph contains an unsupported node kind')
    metadata = tuple(PipelineMetadata(node.id, node.kind, node.operation, node.enable, node.location)
                     for node in graph.nodes if node.id not in stage_ids)
    attachments = tuple(_attachment(graph, node, stage_ids, graph.edges)
                        for node in graph.nodes if node.kind is dependency.NodeKind.WRAPPER)
    mapped = tuple(PipelineDependency(edge.source, edge.target, edge.kind,
                                      stage_ids.get(edge.source), stage_ids.get(edge.target))
                   for edge in graph.edges)
    return PipelineGraph(graph.module, tuple(stages), metadata, mapped, attachments, graph.parameters)
