"""Minimal Unified Semantic Graph data model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from pyslang.ast import (
    ArgumentDirection,
    AssignmentExpression,
    BlockStatement,
    CallExpression,
    ConditionalStatement,
    Expression,
    ExpressionStatement,
    FormalArgumentSymbol,
    GenerateBlockArraySymbol,
    NamedValueExpression,
    ProceduralBlockSymbol,
    RootSymbol,
    StatementBlockKind,
    StatementList,
    SubroutineKind,
    SubroutineSymbol,
    VariableSymbol,
)


@dataclass(frozen=True, slots=True, eq=False)
class ReceiveNode:
    semantic_object: object


@dataclass(frozen=True, slots=True, eq=False)
class SendNode:
    semantic_object: object


@dataclass(frozen=True, slots=True, eq=False)
class AssignNode:
    semantic_object: object


@dataclass(frozen=True, slots=True, eq=False)
class PredicateNode:
    semantic_object: object


USGNode: TypeAlias = ReceiveNode | SendNode | AssignNode | PredicateNode


@dataclass(frozen=True, slots=True)
class DataEdge:
    source: USGNode
    target: USGNode


@dataclass(frozen=True, slots=True)
class ControlEdge:
    source: USGNode
    target: USGNode
    polarity: bool


USGEdge: TypeAlias = DataEdge | ControlEdge


class USGBuilderError(ValueError):
    """A native semantic statement is outside straight-line USG Builder v0."""


class UnifiedSemanticGraph:
    """Semantic nodes and relations with builder insertion order exposed by ``nodes``.

    Builders insert nodes in source or expanded semantic-occurrence order.
    That occurrence order is not represented by a DATA or CONTROL edge.
    """

    __slots__ = ("_nodes", "_data_edges", "_control_edges")

    def __init__(self) -> None:
        self._nodes: list[USGNode] = []
        self._data_edges: list[DataEdge] = []
        self._control_edges: list[ControlEdge] = []

    @property
    def nodes(self) -> tuple[USGNode, ...]:
        """Nodes in builder insertion (source / expanded occurrence) order."""
        return tuple(self._nodes)

    @property
    def data_edges(self) -> tuple[DataEdge, ...]:
        return tuple(self._data_edges)

    @property
    def control_edges(self) -> tuple[ControlEdge, ...]:
        return tuple(self._control_edges)

    def add_node(self, node: USGNode) -> USGNode:
        if not isinstance(node, (ReceiveNode, SendNode, AssignNode, PredicateNode)):
            raise TypeError("expected a USG node")
        if self._contains(node):
            raise ValueError("node is already in this graph")
        self._nodes.append(node)
        return node

    def add_edge(self, edge: USGEdge) -> USGEdge:
        if not isinstance(edge, (DataEdge, ControlEdge)):
            raise TypeError("expected a DATA or CONTROL edge")
        if not self._contains(edge.source) or not self._contains(edge.target):
            raise ValueError("edge endpoints must be nodes in this graph")
        if isinstance(edge, DataEdge):
            self._data_edges.append(edge)
        else:
            self._control_edges.append(edge)
        return edge

    def add_data_edge(self, source: USGNode, target: USGNode) -> DataEdge:
        return self.add_edge(DataEdge(source, target))

    def add_control_edge(
        self, source: USGNode, target: USGNode, polarity: bool
    ) -> ControlEdge:
        if not isinstance(polarity, bool):
            raise TypeError("control edge polarity must be a bool")
        return self.add_edge(ControlEdge(source, target, polarity))

    def _contains(self, node: USGNode) -> bool:
        return any(existing is node for existing in self._nodes)


def build_straight_line_usg(body: object) -> UnifiedSemanticGraph:
    """Build a sequential block or one supported executable statement occurrence."""

    graph = UnifiedSemanticGraph()
    reaching: list[tuple[VariableSymbol, tuple[USGNode, ...]]] = []
    _build_branch(graph, body, reaching, ())
    return graph


def build_process_usgs(
    root: RootSymbol,
) -> tuple[tuple[ProceduralBlockSymbol, UnifiedSemanticGraph], ...]:
    """Build one independent USG for every elaborated native procedural block.

    Result ordering follows native hierarchy traversal and generate-array entry
    order for deterministic enumeration only; it is not a semantic ordering
    relation between processes.
    """

    if not isinstance(root, RootSymbol):
        raise USGBuilderError("expected a native RootSymbol")

    items: list[object] = []
    root.visit(items.append)
    generated_processes = _generated_processes(items)
    generated_ids = {id(process) for process in generated_processes}
    processes: list[ProceduralBlockSymbol] = []
    seen_ids: set[int] = set()

    for item in items:
        if isinstance(item, GenerateBlockArraySymbol):
            for process in _processes_in_generate_array(item):
                _append_process(processes, seen_ids, process)
        elif isinstance(item, ProceduralBlockSymbol) and id(item) not in generated_ids:
            _append_process(processes, seen_ids, item)

    return tuple((process, build_straight_line_usg(process.body)) for process in processes)


def _generated_processes(items: list[object]) -> tuple[ProceduralBlockSymbol, ...]:
    processes: list[ProceduralBlockSymbol] = []
    seen_ids: set[int] = set()
    for item in items:
        if isinstance(item, GenerateBlockArraySymbol):
            for process in _processes_in_generate_array(item):
                _append_process(processes, seen_ids, process)
    return tuple(processes)


def _processes_in_generate_array(
    array: GenerateBlockArraySymbol,
) -> tuple[ProceduralBlockSymbol, ...]:
    processes: list[ProceduralBlockSymbol] = []
    seen_ids: set[int] = set()
    for entry in array.entries:
        items: list[object] = []
        entry.visit(items.append)
        for item in items:
            if isinstance(item, ProceduralBlockSymbol):
                _append_process(processes, seen_ids, item)
    return tuple(processes)


def _append_process(
    processes: list[ProceduralBlockSymbol], seen_ids: set[int], process: ProceduralBlockSymbol
) -> None:
    if id(process) not in seen_ids:
        processes.append(process)
        seen_ids.add(id(process))


def _block_statements(block: BlockStatement) -> tuple[object, ...]:
    if block.blockKind is not StatementBlockKind.Sequential:
        raise USGBuilderError("only sequential BlockStatement traversal is supported")
    return block.body.list if isinstance(block.body, StatementList) else (block.body,)


def _build_statements(
    graph: UnifiedSemanticGraph,
    statements: tuple[object, ...],
    reaching: list[tuple[VariableSymbol, tuple[USGNode, ...]]],
    controls: tuple[tuple[PredicateNode, bool], ...],
) -> None:
    for statement in statements:
        if isinstance(statement, ConditionalStatement):
            _build_conditional(graph, statement, reaching, controls)
            continue
        if not isinstance(statement, ExpressionStatement):
            raise USGBuilderError(
                f"unsupported executable statement: {type(statement).__name__}"
            )
        expression = statement.expr
        if isinstance(expression, CallExpression):
            node, definitions, uses = _build_call_node(expression)
        elif isinstance(expression, AssignmentExpression):
            node, definitions, uses = _build_assign_node(expression)
        else:
            raise USGBuilderError(
                f"unsupported executable statement: {type(expression).__name__}"
            )

        graph.add_node(node)
        for variable in uses:
            for source in _reaching_definitions(reaching, variable):
                graph.add_data_edge(source, node)
        for predicate, polarity in controls:
            graph.add_control_edge(predicate, node, polarity)
        for variable in definitions:
            _set_reaching_definition(reaching, variable, node)


def _build_conditional(
    graph: UnifiedSemanticGraph,
    statement: ConditionalStatement,
    reaching: list[tuple[VariableSymbol, tuple[USGNode, ...]]],
    controls: tuple[tuple[PredicateNode, bool], ...],
) -> None:
    if len(statement.conditions) != 1 or statement.conditions[0].pattern is not None:
        raise USGBuilderError("unsupported conditional form")

    predicate = PredicateNode(statement.conditions[0].expr)
    graph.add_node(predicate)
    for variable in _expression_variables(statement.conditions[0].expr):
        for source in _reaching_definitions(reaching, variable):
            graph.add_data_edge(source, predicate)
    for controlling_predicate, polarity in controls:
        graph.add_control_edge(controlling_predicate, predicate, polarity)

    true_reaching = reaching.copy()
    _build_branch(
        graph, statement.ifTrue, true_reaching, controls + ((predicate, True),)
    )
    if statement.ifFalse is not None:
        false_reaching = reaching.copy()
        _build_branch(
            graph, statement.ifFalse, false_reaching, controls + ((predicate, False),)
        )
        reaching[:] = _merge_reaching_definitions(true_reaching, false_reaching)
    else:
        reaching[:] = _merge_reaching_definitions(reaching, true_reaching)


def _build_branch(
    graph: UnifiedSemanticGraph,
    statement: object,
    reaching: list[tuple[VariableSymbol, tuple[USGNode, ...]]],
    controls: tuple[tuple[PredicateNode, bool], ...],
) -> None:
    if isinstance(statement, BlockStatement):
        _build_statements(graph, _block_statements(statement), reaching, controls)
        return
    _build_statements(graph, (statement,), reaching, controls)


def _build_call_node(
    call: CallExpression,
) -> tuple[ReceiveNode | SendNode, tuple[VariableSymbol, ...], tuple[VariableSymbol, ...]]:
    subroutine = call.subroutine
    if not isinstance(subroutine, SubroutineSymbol):
        raise USGBuilderError("communication call has no resolved SubroutineSymbol")
    if call.isSystemCall or subroutine.subroutineKind is not SubroutineKind.Task:
        raise USGBuilderError("unsupported executable statement: expected a resolved interface task call")
    endpoint = subroutine.containingInstance
    if not endpoint.parentInstance.isInterface:
        raise USGBuilderError("unsupported executable statement: task is not owned by an interface")
    if len(subroutine.arguments) != 1 or len(call.arguments) != 1:
        raise USGBuilderError("unsupported communication task signature")
    formal = subroutine.arguments[0]
    if not isinstance(formal, FormalArgumentSymbol):
        raise USGBuilderError("unsupported communication task signature")

    actual = call.arguments[0]
    if subroutine.name == "Receive":
        if formal.direction is not ArgumentDirection.Out:
            raise USGBuilderError("Receive task must have one output formal")
        if not isinstance(actual, AssignmentExpression):
            raise USGBuilderError("unsupported Receive output-actual representation")
        return ReceiveNode(call), (_lvalue_variable(actual.left),), ()
    if subroutine.name == "Send":
        if formal.direction is not ArgumentDirection.In:
            raise USGBuilderError("Send task must have one input formal")
        return SendNode(call), (), _expression_variables(actual)
    raise USGBuilderError("unsupported interface task: expected Receive or Send")


def _build_assign_node(
    assignment: AssignmentExpression,
) -> tuple[AssignNode, tuple[VariableSymbol, ...], tuple[VariableSymbol, ...]]:
    if assignment.isNonBlocking or assignment.isCompound or assignment.timingControl is not None:
        raise USGBuilderError("unsupported assignment form")
    return AssignNode(assignment), (_lvalue_variable(assignment.left),), _expression_variables(assignment.right)


def _lvalue_variable(expression: Expression) -> VariableSymbol:
    if not isinstance(expression, NamedValueExpression):
        raise USGBuilderError("unsupported assignment target")
    symbol = expression.getSymbolReference()
    if not isinstance(symbol, VariableSymbol):
        raise USGBuilderError("assignment target is not a VariableSymbol")
    return symbol


def _expression_variables(expression: Expression) -> tuple[VariableSymbol, ...]:
    variables: list[VariableSymbol] = []

    def visit(item: object) -> None:
        if not isinstance(item, NamedValueExpression):
            return
        symbol = item.getSymbolReference()
        if isinstance(symbol, VariableSymbol) and not any(symbol is known for known in variables):
            variables.append(symbol)

    expression.visit(visit)
    return tuple(variables)


def _reaching_definitions(
    reaching: list[tuple[VariableSymbol, tuple[USGNode, ...]]], variable: VariableSymbol
) -> tuple[USGNode, ...]:
    for known, nodes in reaching:
        if known is variable:
            return nodes
    return ()


def _set_reaching_definition(
    reaching: list[tuple[VariableSymbol, tuple[USGNode, ...]]],
    variable: VariableSymbol,
    node: USGNode,
) -> None:
    for index, (known, _) in enumerate(reaching):
        if known is variable:
            reaching[index] = (variable, (node,))
            return
    reaching.append((variable, (node,)))


def _merge_reaching_definitions(
    *environments: list[tuple[VariableSymbol, tuple[USGNode, ...]]],
) -> list[tuple[VariableSymbol, tuple[USGNode, ...]]]:
    merged: list[tuple[VariableSymbol, list[USGNode]]] = []
    for environment in environments:
        for variable, nodes in environment:
            for index, (known, known_nodes) in enumerate(merged):
                if known is variable:
                    for node in nodes:
                        if not any(node is existing for existing in known_nodes):
                            known_nodes.append(node)
                    break
            else:
                merged.append((variable, list(nodes)))

    return [
        (variable, tuple(nodes))
        for variable, nodes in merged
    ]
