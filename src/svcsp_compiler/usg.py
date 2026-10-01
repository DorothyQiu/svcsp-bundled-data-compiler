"""Minimal Unified Semantic Graph data model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from pyslang.ast import (
    ArgumentDirection,
    AssignmentExpression,
    BlockStatement,
    CallExpression,
    Expression,
    ExpressionStatement,
    FormalArgumentSymbol,
    NamedValueExpression,
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


USGEdge: TypeAlias = DataEdge | ControlEdge


class USGBuilderError(ValueError):
    """A native semantic statement is outside straight-line USG Builder v0."""


class UnifiedSemanticGraph:
    """An insertion-ordered collection of semantic nodes and relations."""

    __slots__ = ("_nodes", "_data_edges", "_control_edges")

    def __init__(self) -> None:
        self._nodes: list[USGNode] = []
        self._data_edges: list[DataEdge] = []
        self._control_edges: list[ControlEdge] = []

    @property
    def nodes(self) -> tuple[USGNode, ...]:
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

    def add_control_edge(self, source: USGNode, target: USGNode) -> ControlEdge:
        return self.add_edge(ControlEdge(source, target))

    def _contains(self, node: USGNode) -> bool:
        return any(existing is node for existing in self._nodes)


def build_straight_line_usg(block: BlockStatement) -> UnifiedSemanticGraph:
    """Build Receive, Assign, and Send DATA flow from one sequential block."""

    if not isinstance(block, BlockStatement):
        raise USGBuilderError("unsupported executable statement: expected a native BlockStatement")
    if block.blockKind is not StatementBlockKind.Sequential:
        raise USGBuilderError("only sequential BlockStatement traversal is supported")

    graph = UnifiedSemanticGraph()
    latest: list[tuple[VariableSymbol, USGNode]] = []
    statements = block.body.list if isinstance(block.body, StatementList) else (block.body,)
    for statement in statements:
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
            source = _latest_definition(latest, variable)
            if source is not None:
                graph.add_data_edge(source, node)
        for variable in definitions:
            _set_latest_definition(latest, variable, node)
    return graph


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


def _latest_definition(
    latest: list[tuple[VariableSymbol, USGNode]], variable: VariableSymbol
) -> USGNode | None:
    for known, node in latest:
        if known is variable:
            return node
    return None


def _set_latest_definition(
    latest: list[tuple[VariableSymbol, USGNode]], variable: VariableSymbol, node: USGNode
) -> None:
    for index, (known, _) in enumerate(latest):
        if known is variable:
            latest[index] = (variable, node)
            return
    latest.append((variable, node))
