"""Phase 4: behavioral dependency analysis for normalized communication."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import behavioral_ir as behavioral
from . import communication_normalization as normalization


class NodeKind(str, Enum):
    OPERATION = 'operation'
    CONTROL = 'control'
    PARALLEL_JOIN = 'parallel_join'
    ENABLE = 'enable'
    WRAPPER = 'wrapper'


class DependencyKind(str, Enum):
    DATA = 'data'
    SEQUENCE = 'sequence'
    CONTROL = 'control'
    COMMUNICATION = 'communication'
    PARALLEL_JOIN = 'parallel_join'


@dataclass(frozen=True)
class DependencyNode:
    """A uniquely identified behavioral event or structural dependency point."""

    id: str
    kind: NodeKind
    label: str
    operation: object | None = None
    endpoint: behavioral.ChannelEndpoint | None = None
    variable: behavioral.Variable | None = None
    enable: normalization.Enable | None = None
    location: behavioral.SourceLocation | None = None


@dataclass(frozen=True)
class DependencyEdge:
    source: str
    target: str
    kind: DependencyKind


@dataclass(frozen=True)
class DependencyGraph:
    module: str
    nodes: tuple[DependencyNode, ...]
    edges: tuple[DependencyEdge, ...]
    parameters: tuple[behavioral.Parameter, ...] = ()

    def nodes_for(self, operation: object) -> tuple[DependencyNode, ...]:
        """Return graph nodes that trace to an exact normalized IR object."""
        return tuple(node for node in self.nodes if node.operation is operation)


class DependencyAnalysisError(ValueError):
    """A normalized module could not be represented as a dependency graph."""


@dataclass(frozen=True)
class GuardLiteral:
    expression: behavioral.Expression
    positive: bool


Guard = frozenset[GuardLiteral]


@dataclass(frozen=True)
class ReachingDefinition:
    node: str
    validity_guard: Guard


@dataclass
class _Flow:
    entries: set[str]
    exits: set[str]
    definitions: dict[behavioral.Variable, set[ReachingDefinition]]


def _variables(expression: behavioral.Expression) -> set[behavioral.Variable]:
    variables = {expression.variable} if expression.variable else set()
    for operand in expression.operands:
        variables |= _variables(operand)
    return variables


def _target_variable(target: behavioral.Variable | behavioral.Expression) -> behavioral.Variable | None:
    return target if isinstance(target, behavioral.Variable) else target.variable


def _extend_guard(guard: Guard, expression: behavioral.Expression, positive: bool = True) -> Guard:
    """Represent compiler-produced conjunctions as a set of branch literals."""
    if expression.form == 'binary' and expression.operator == '&&' and positive:
        return _extend_guard(_extend_guard(guard, expression.operands[0]), expression.operands[1])
    if expression.form == 'unary' and expression.operator == '!':
        return _extend_guard(guard, expression.operands[0], not positive)
    return guard | {GuardLiteral(expression, positive)}


def _guard_for(expression: behavioral.Expression) -> Guard:
    return _extend_guard(frozenset(), expression)


def _implies(current: Guard, required: Guard) -> bool:
    return required <= current


def _describe_guard(guard: Guard) -> str:
    if not guard:
        return 'unconditional path'
    return ' && '.join(sorted(('' if literal.positive else '!') +
                              (literal.expression.value or literal.expression.form)
                              for literal in guard))


class _Analyzer:
    def __init__(self, module: normalization.NormalizedModule) -> None:
        self.module = module
        self.nodes: list[DependencyNode] = []
        self.edges: list[DependencyEdge] = []
        self._edge_keys: set[tuple[str, str, DependencyKind]] = set()
        self._next_id = 0
        self.wrappers = {
            (wrapper.location.file, wrapper.location.line, wrapper.location.column): wrapper
            for wrapper in module.wrappers
            if wrapper.location is not None
        }
        self.conditional_receive_guards: dict[behavioral.Variable, set[Guard]] = {}
        for wrapper in module.wrappers:
            if isinstance(wrapper, normalization.NormalizedReceive):
                target = _target_variable(wrapper.target)
                if target:
                    self.conditional_receive_guards.setdefault(target, set()).add(_guard_for(wrapper.enable.condition))

    def node(self, kind: NodeKind, label: str, *, operation: object | None = None,
             endpoint: behavioral.ChannelEndpoint | None = None,
             variable: behavioral.Variable | None = None,
             enable: normalization.Enable | None = None,
             location: behavioral.SourceLocation | None = None) -> str:
        node_id = f'n{self._next_id}'
        self._next_id += 1
        self.nodes.append(DependencyNode(node_id, kind, label, operation, endpoint, variable, enable, location))
        return node_id

    def edge(self, source: str, target: str, kind: DependencyKind) -> None:
        key = (source, target, kind)
        if key not in self._edge_keys:
            self._edge_keys.add(key)
            self.edges.append(DependencyEdge(source, target, kind))

    def read(self, variables: set[behavioral.Variable],
             definitions: dict[behavioral.Variable, set[ReachingDefinition]], target: str,
             current_guard: Guard) -> None:
        for variable in variables:
            reaching = definitions.get(variable, set())
            if not reaching:
                for validity_guard in self.conditional_receive_guards.get(variable, set()):
                    if not _implies(current_guard, validity_guard):
                        raise DependencyAnalysisError(
                            f'invalid use of {variable.name}: {_describe_guard(current_guard)} does not prove '
                            f'conditional receive validity {_describe_guard(validity_guard)}'
                        )
            for definition in reaching:
                if not _implies(current_guard, definition.validity_guard):
                    raise DependencyAnalysisError(
                        f'invalid use of {variable.name}: {_describe_guard(current_guard)} does not prove '
                        f'definition validity {_describe_guard(definition.validity_guard)}'
                    )
                self.edge(definition.node, target, DependencyKind.DATA)

    def wrapper_for(self, process: behavioral.Skip) -> normalization.Wrapper | None:
        location = process.location
        if location is None:
            return None
        return self.wrappers.pop((location.file, location.line, location.column), None)

    def wrapper_flow(self, skip: behavioral.Skip, wrapper: normalization.Wrapper,
                     definitions: dict[behavioral.Variable, set[ReachingDefinition]], controls: set[str],
                     current_guard: Guard) -> _Flow:
        skip_id = self.node(NodeKind.OPERATION, 'skip', operation=skip, location=skip.location)
        enable_id = self.node(NodeKind.ENABLE, 'enable', operation=wrapper.enable,
                              enable=wrapper.enable, location=wrapper.enable.location)
        self.read(_variables(wrapper.enable.condition), definitions, enable_id, current_guard)
        wrapper_label = 'normalized_receive' if isinstance(wrapper, normalization.NormalizedReceive) else 'normalized_send'
        wrapper_id = self.node(NodeKind.WRAPPER, wrapper_label, operation=wrapper,
                               endpoint=wrapper.endpoint, enable=wrapper.enable, location=wrapper.location)
        for control in controls:
            self.edge(control, enable_id, DependencyKind.CONTROL)
            self.edge(control, wrapper_id, DependencyKind.CONTROL)
        self.edge(enable_id, wrapper_id, DependencyKind.CONTROL)
        updated = {variable: set(nodes) for variable, nodes in definitions.items()}
        if isinstance(wrapper, normalization.NormalizedReceive):
            self.edge(wrapper_id, skip_id, DependencyKind.COMMUNICATION)
            target = _target_variable(wrapper.target)
            if target:
                updated[target] = {ReachingDefinition(wrapper_id, _guard_for(wrapper.enable.condition))}
            return _Flow({wrapper_id}, {skip_id}, updated)
        self.read(_variables(wrapper.value), definitions, wrapper_id, current_guard)
        self.edge(skip_id, wrapper_id, DependencyKind.COMMUNICATION)
        return _Flow({skip_id}, {wrapper_id}, updated)

    def leaf(self, process: behavioral.Process,
             definitions: dict[behavioral.Variable, set[ReachingDefinition]], controls: set[str],
             current_guard: Guard) -> _Flow:
        if isinstance(process, behavioral.Skip):
            wrapper = self.wrapper_for(process)
            if wrapper:
                return self.wrapper_flow(process, wrapper, definitions, controls, current_guard)
            node_id = self.node(NodeKind.OPERATION, 'skip', operation=process, location=process.location)
            return _Flow({node_id}, {node_id}, definitions)
        if isinstance(process, behavioral.Receive):
            node_id = self.node(NodeKind.OPERATION, 'receive', operation=process,
                                endpoint=process.channel, variable=_target_variable(process.target), location=process.location)
            updated = {variable: set(nodes) for variable, nodes in definitions.items()}
            target = _target_variable(process.target)
            if target:
                updated[target] = {ReachingDefinition(node_id, current_guard)}
            return _Flow({node_id}, {node_id}, updated)
        if isinstance(process, behavioral.Assign):
            node_id = self.node(NodeKind.OPERATION, 'assign', operation=process,
                                variable=_target_variable(process.target), location=process.location)
            self.read(_variables(process.value), definitions, node_id, current_guard)
            updated = {variable: set(nodes) for variable, nodes in definitions.items()}
            target = _target_variable(process.target)
            if target:
                updated[target] = {ReachingDefinition(node_id, current_guard)}
            return _Flow({node_id}, {node_id}, updated)
        if isinstance(process, behavioral.Send):
            node_id = self.node(NodeKind.OPERATION, 'send', operation=process,
                                endpoint=process.channel, location=process.location)
            self.read(_variables(process.value), definitions, node_id, current_guard)
            return _Flow({node_id}, {node_id}, definitions)
        raise DependencyAnalysisError(f'unsupported behavioral leaf {type(process).__name__}')

    @staticmethod
    def merge_definitions(flows: list[_Flow]) -> dict[behavioral.Variable, set[ReachingDefinition]]:
        merged: dict[behavioral.Variable, set[ReachingDefinition]] = {}
        for flow in flows:
            for variable, definitions in flow.definitions.items():
                merged.setdefault(variable, set()).update(definitions)
        return merged

    def process(self, process: behavioral.Process,
                definitions: dict[behavioral.Variable, set[ReachingDefinition]], controls: set[str] | None = None,
                current_guard: Guard = frozenset()) -> _Flow:
        controls = controls or set()
        if isinstance(process, behavioral.Sequence):
            current: _Flow | None = None
            for item in process.items:
                flow = self.process(item, current.definitions if current else definitions, controls, current_guard)
                if current:
                    for previous in current.exits:
                        for entry in flow.entries:
                            self.edge(previous, entry, DependencyKind.SEQUENCE)
                current = flow
            if current:
                return current
            skip = behavioral.Skip(process.location)
            return self.leaf(skip, definitions, controls, current_guard)
        if isinstance(process, behavioral.Parallel):
            branches = [self.process(branch, {variable: set(nodes) for variable, nodes in definitions.items()}, controls,
                                     current_guard)
                        for branch in process.branches]
            join_id = self.node(NodeKind.PARALLEL_JOIN, 'parallel_join', operation=process, location=process.location)
            for branch in branches:
                for exit_node in branch.exits:
                    self.edge(exit_node, join_id, DependencyKind.PARALLEL_JOIN)
            entries = set().union(*(branch.entries for branch in branches)) if branches else {join_id}
            return _Flow(entries, {join_id}, self.merge_definitions(branches) if branches else definitions)
        if isinstance(process, behavioral.If):
            control_id = self.node(NodeKind.CONTROL, 'if', operation=process, location=process.location)
            self.read(_variables(process.condition), definitions, control_id, current_guard)
            branch_controls = controls | {control_id}
            then_flow = self.process(process.then_branch, {variable: set(nodes) for variable, nodes in definitions.items()},
                                     branch_controls, _extend_guard(current_guard, process.condition))
            else_flow = self.process(process.else_branch, {variable: set(nodes) for variable, nodes in definitions.items()},
                                     branch_controls, _extend_guard(current_guard, process.condition, False))
            for branch in (then_flow, else_flow):
                for entry in branch.entries:
                    self.edge(control_id, entry, DependencyKind.CONTROL)
            return _Flow({control_id}, then_flow.exits | else_flow.exits,
                         self.merge_definitions([then_flow, else_flow]))
        return self.leaf(process, definitions, controls, current_guard)

    def run(self) -> DependencyGraph:
        flow = self.process(self.module.body, {})
        if self.wrappers:
            raise DependencyAnalysisError('normalized wrapper has no BODY counterpart')
        return DependencyGraph(self.module.name, tuple(self.nodes), tuple(self.edges), self.module.parameters)


def analyze_dependencies(module: normalization.NormalizedModule) -> DependencyGraph:
    """Build a behavioral dependency graph without scheduling or lowering hardware."""
    if not isinstance(module, normalization.NormalizedModule):
        raise DependencyAnalysisError('expected a NormalizedModule')
    return _Analyzer(module).run()
