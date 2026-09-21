"""Phase 6: bundled-data microarchitecture IR and template selection."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import behavioral_ir as behavioral
from . import communication_normalization as normalization
from . import dependency_analysis as dependency
from . import pipeline_synthesis as pipeline


class ControllerKind(str, Enum):
    LINEAR = 'linear'
    JOIN = 'join'
    CONDITIONAL_SEND = 'conditional_send'
    CONDITIONAL_RECV = 'conditional_recv'


@dataclass(frozen=True)
class StorageRequirement:
    """A symbolic storage decision with no concrete primitive selection."""

    required: bool | None
    implementation: None = None
    basis: str = 'undecided'


@dataclass(frozen=True)
class HandshakePort:
    """A symbolic token handshake boundary; no req/ack circuit is selected."""

    identity: str
    peer_stage: str
    dependency_kind: dependency.DependencyKind


@dataclass(frozen=True)
class MatchedDelayRequirement:
    """A symbolic delay requirement with no physical timing value."""

    symbol: str
    value: None = None


@dataclass(frozen=True)
class MicroarchitectureStage:
    id: str
    controller: ControllerKind
    body_operations: tuple[dependency.DependencyNode, ...]
    combinational_logic: tuple[behavioral.Expression, ...]
    storage: StorageRequirement
    handshake_inputs: tuple[HandshakePort, ...]
    handshake_outputs: tuple[HandshakePort, ...]
    matched_delay: MatchedDelayRequirement | None
    wrapper_attachments: tuple[str, ...] = ()
    endpoint: behavioral.ChannelEndpoint | None = None
    variable: behavioral.Variable | None = None
    location: behavioral.SourceLocation | None = None
    upstream_boundary: dependency.DependencyNode | None = None
    downstream_boundary: dependency.DependencyNode | None = None


@dataclass(frozen=True)
class MicroarchitectureDependency:
    """Topology retained from a PipelineDependency without implementation choices."""

    source_node: str
    target_node: str
    kind: dependency.DependencyKind
    source_stage: str | None = None
    target_stage: str | None = None


@dataclass(frozen=True)
class MicroarchitectureMetadata:
    id: str
    kind: dependency.NodeKind
    operation: object | None = None
    enable: object | None = None
    location: behavioral.SourceLocation | None = None


@dataclass(frozen=True)
class MicroarchitectureWrapper:
    """A conditional external communication template attached to a BODY stage."""

    id: str
    controller: ControllerKind
    attached_to: str
    endpoint: behavioral.ChannelEndpoint
    enable: normalization.Enable
    storage: StorageRequirement
    matched_delay: MatchedDelayRequirement | None = None
    location: behavioral.SourceLocation | None = None


@dataclass(frozen=True)
class MicroarchitectureGraph:
    module: str
    stages: tuple[MicroarchitectureStage, ...]
    dependencies: tuple[MicroarchitectureDependency, ...]
    metadata: tuple[MicroarchitectureMetadata, ...]
    wrappers: tuple[MicroarchitectureWrapper, ...]
    parameters: tuple[behavioral.Parameter, ...] = ()

    def stage(self, stage_id: str) -> MicroarchitectureStage | None:
        return next((stage for stage in self.stages if stage.id == stage_id), None)


class MicroarchitectureError(ValueError):
    """A conservative controller template cannot represent a pipeline graph."""


def _controller(stage: pipeline.PipelineStage) -> ControllerKind:
    if stage.kind is pipeline.StageKind.JOIN:
        return ControllerKind.JOIN
    return ControllerKind.LINEAR


def _wrapper_controller(attachment: pipeline.WrapperAttachment) -> ControllerKind:
    direction = attachment.direction
    if direction == 'into_body':
        return ControllerKind.CONDITIONAL_RECV
    if direction == 'from_body':
        return ControllerKind.CONDITIONAL_SEND
    raise MicroarchitectureError(f'wrapper {attachment.wrapper_node} has an unknown direction {direction}')


def _is_anchor(stage: pipeline.PipelineStage, logic: tuple[behavioral.Expression, ...]) -> bool:
    return (stage.kind is pipeline.StageKind.JOIN or all(operation.label == 'skip' for operation in stage.operations)) \
        and not logic


def _storage(stage: pipeline.PipelineStage, logic: tuple[behavioral.Expression, ...]) -> StorageRequirement:
    if _is_anchor(stage, logic):
        return StorageRequirement(False, basis='topology_only')
    return StorageRequirement(True, basis='datapath_stage')


def _nontrivial(expression: behavioral.Expression) -> bool:
    return expression.form not in {'name', 'literal'}


def _body_logic(stage: pipeline.PipelineStage, attachments: tuple[pipeline.WrapperAttachment, ...],
                wrapper_operations: dict[str, object]) -> tuple[behavioral.Expression, ...]:
    expressions: list[behavioral.Expression] = []
    for node in stage.operations:
        operation = node.operation
        if isinstance(operation, (behavioral.Assign, behavioral.Send)) and _nontrivial(operation.value):
            expressions.append(operation.value)
    for attachment in attachments:
        operation = wrapper_operations.get(attachment.wrapper_node)
        if isinstance(operation, normalization.NormalizedSend) and _nontrivial(operation.value):
            expressions.append(operation.value)
    return tuple(expressions)


def _matched_delay(stage: pipeline.PipelineStage,
                   logic: tuple[behavioral.Expression, ...]) -> MatchedDelayRequirement | None:
    if _is_anchor(stage, logic) or not logic:
        return None
    return MatchedDelayRequirement(f'matched_delay_{stage.id}')


def select_microarchitecture(graph: pipeline.PipelineGraph) -> MicroarchitectureGraph:
    """Select conservative symbolic controller templates for a PipelineGraph."""
    if not isinstance(graph, pipeline.PipelineGraph):
        raise MicroarchitectureError('expected a PipelineGraph')
    stage_ids = {stage.id for stage in graph.stages}
    if len(stage_ids) != len(graph.stages):
        raise MicroarchitectureError('pipeline graph has duplicate stage identities')
    if any((edge.source_stage and edge.source_stage not in stage_ids) or
           (edge.target_stage and edge.target_stage not in stage_ids) for edge in graph.dependencies):
        raise MicroarchitectureError('pipeline dependency refers to an unknown stage')
    by_stage: dict[str, list[pipeline.WrapperAttachment]] = {stage.id: [] for stage in graph.stages}
    for attachment in graph.attachments:
        if attachment.body_stage not in by_stage:
            raise MicroarchitectureError(f'wrapper attachment refers to unknown stage {attachment.body_stage}')
        by_stage[attachment.body_stage].append(attachment)
    if any(len(attachments) > 1 for attachments in by_stage.values()):
        raise MicroarchitectureError('a BODY stage has multiple wrapper attachments')
    if any(stage.kind is pipeline.StageKind.JOIN and by_stage[stage.id] for stage in graph.stages):
        raise MicroarchitectureError('a join stage cannot host a communication wrapper')
    wrapper_operations = {item.id: item.operation for item in graph.metadata
                          if item.kind is dependency.NodeKind.WRAPPER}

    inputs: dict[str, list[HandshakePort]] = {stage.id: [] for stage in graph.stages}
    outputs: dict[str, list[HandshakePort]] = {stage.id: [] for stage in graph.stages}
    for index, edge in enumerate(graph.dependencies):
        if edge.source_stage and edge.target_stage and edge.source_stage != edge.target_stage:
            identity = f'handshake_{index}'
            outputs[edge.source_stage].append(HandshakePort(identity, edge.target_stage, edge.kind))
            inputs[edge.target_stage].append(HandshakePort(identity, edge.source_stage, edge.kind))

    wrappers = tuple(MicroarchitectureWrapper(
        id=attachment.wrapper_node,
        controller=_wrapper_controller(attachment),
        attached_to=attachment.body_stage,
        endpoint=attachment.endpoint,
        enable=attachment.enable,
        storage=StorageRequirement(None, basis='wrapper_template_undecided'),
        location=attachment.location,
    ) for attachment in graph.attachments)
    stages = []
    for stage in graph.stages:
        attachments = tuple(by_stage[stage.id])
        logic = _body_logic(stage, attachments, wrapper_operations)
        stages.append(MicroarchitectureStage(
            id=stage.id,
            controller=_controller(stage),
            body_operations=stage.operations,
            combinational_logic=logic,
            storage=_storage(stage, logic),
            handshake_inputs=tuple(inputs[stage.id]),
            handshake_outputs=tuple(outputs[stage.id]),
            matched_delay=_matched_delay(stage, logic),
            wrapper_attachments=tuple(attachment.wrapper_node for attachment in attachments),
            endpoint=stage.endpoint,
            variable=stage.variable,
            location=stage.location,
            upstream_boundary=stage.upstream_boundary,
            downstream_boundary=stage.downstream_boundary,
        ))
    dependencies = tuple(MicroarchitectureDependency(edge.source_node, edge.target_node, edge.kind,
                                                      edge.source_stage, edge.target_stage)
                         for edge in graph.dependencies)
    metadata = tuple(MicroarchitectureMetadata(item.id, item.kind, item.operation, item.enable, item.location)
                     for item in graph.metadata)
    return MicroarchitectureGraph(graph.module, tuple(stages), dependencies, metadata, wrappers, graph.parameters)
