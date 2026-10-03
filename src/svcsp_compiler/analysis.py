"""Read-only semantic facts derived from a Unified Semantic Graph."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pyslang.ast import (
    AssignmentExpression,
    CallExpression,
    Expression,
    NamedValueExpression,
    ParameterSymbol,
    VariableSymbol,
)
from pyslang.syntax import ImplicitAnsiPortSyntax

from .usg import (
    AssignNode,
    ControlEdge,
    PredicateNode,
    ReceiveNode,
    SendNode,
    USGNode,
    UnifiedSemanticGraph,
)


CommunicationNode = ReceiveNode | SendNode
DefinitionNode = ReceiveNode | AssignNode
SemanticValueSymbol = VariableSymbol | ParameterSymbol
_ControlPath = tuple[tuple[PredicateNode, bool], ...]
_ReachingDefinitions = list[tuple[VariableSymbol, tuple[DefinitionNode, ...], bool]]


class ValueOrigin(str, Enum):
    """Semantic origin of a value at a specific use occurrence."""

    GRAPH_LOCAL = "graph-local"
    PORT_ENTRY = "port-entry"
    LOCAL_ENTRY = "local-entry"
    PARAMETER = "parameter"


@dataclass(frozen=True, slots=True)
class ValueUse:
    """One native semantic value use and its graph-local availability."""

    node: USGNode
    symbol: SemanticValueSymbol
    origin: ValueOrigin
    reaching_producers: tuple[DefinitionNode, ...]
    entry_value_may_reach: bool


@dataclass(frozen=True, slots=True)
class ExitReachingValue:
    """Graph-local definitions and entry reachability for one process-exit value."""

    symbol: VariableSymbol
    graph_local_definitions: tuple[DefinitionNode, ...]
    entry_value_may_reach: bool


@dataclass(frozen=True, slots=True)
class AnalysisFacts:
    """Read-only facts collected during one USG occurrence-order traversal."""

    communication_predecessors: tuple[tuple[CommunicationNode, tuple[CommunicationNode, ...]], ...]
    incoming_communication_frontiers: tuple[
        tuple[USGNode, tuple[CommunicationNode, ...]], ...
    ]
    definitions: tuple[tuple[DefinitionNode, tuple[VariableSymbol, ...]], ...]
    uses: tuple[tuple[USGNode, tuple[SemanticValueSymbol, ...]], ...]
    reaching_producers: tuple[
        tuple[USGNode, VariableSymbol, tuple[DefinitionNode, ...]], ...
    ]
    uses_without_graph_local_producer: tuple[tuple[USGNode, SemanticValueSymbol], ...]
    value_uses: tuple[ValueUse, ...]
    definition_consumers: tuple[tuple[DefinitionNode, tuple[ValueUse, ...]], ...]
    exit_reaching_values: tuple[ExitReachingValue, ...]

    def predecessors_of(self, communication: CommunicationNode) -> tuple[CommunicationNode, ...]:
        """Return the possible preceding communications for ``communication``."""

        for known, predecessors in self.communication_predecessors:
            if known is communication:
                return predecessors
        raise KeyError("communication node is not present in these analysis facts")

    def incoming_frontier_of(self, node: USGNode) -> tuple[CommunicationNode, ...]:
        """Return possible immediately preceding communications before ``node``."""

        for known, frontier in self.incoming_communication_frontiers:
            if known is node:
                return frontier
        raise KeyError("USG node is not present in these analysis facts")

    def definitions_of(self, node: DefinitionNode) -> tuple[VariableSymbol, ...]:
        """Return variables defined by a Receive or Assign occurrence."""

        for known, definitions in self.definitions:
            if known is node:
                return definitions
        raise KeyError("definition node is not present in these analysis facts")

    def uses_of(self, node: USGNode) -> tuple[SemanticValueSymbol, ...]:
        """Return variables read by an Assign, Predicate, or Send occurrence."""

        for known, uses in self.uses:
            if known is node:
                return uses
        raise KeyError("use node is not present in these analysis facts")

    def producers_of(
        self, node: USGNode, variable: SemanticValueSymbol
    ) -> tuple[DefinitionNode, ...]:
        """Return graph-local definitions reaching one native variable use."""

        for known, used_variable, producers in self.reaching_producers:
            if known is node and used_variable is variable:
                return producers
        if isinstance(variable, ParameterSymbol):
            for value_use in self.value_uses:
                if value_use.node is node and value_use.symbol is variable:
                    return ()
        raise KeyError("variable is not used by this node in these analysis facts")

    def value_use_of(self, node: USGNode, symbol: SemanticValueSymbol) -> ValueUse:
        """Return the origin and reaching producers for one native semantic use."""

        for value_use in self.value_uses:
            if value_use.node is node and value_use.symbol is symbol:
                return value_use
        raise KeyError("semantic value is not used by this node in these analysis facts")

    def consumers_of(self, definition: DefinitionNode) -> tuple[ValueUse, ...]:
        """Return semantic uses that may consume this exact reaching definition."""

        for known, consumers in self.definition_consumers:
            if known is definition:
                return consumers
        raise KeyError("definition node is not present in these analysis facts")

    def exit_reaching_of(self, symbol: VariableSymbol) -> ExitReachingValue:
        """Return graph-local and entry-value reachability for ``symbol`` at exit."""

        for value in self.exit_reaching_values:
            if value.symbol is symbol:
                return value
        raise KeyError("variable is not present in these process-exit analysis facts")


class USGAnalysisError(ValueError):
    """USG occurrence/control structure cannot be analyzed as structured paths."""


def analyze_usg(graph: UnifiedSemanticGraph) -> AnalysisFacts:
    """Derive read-only communication and value-availability facts from ``graph``."""

    nodes = graph.nodes
    paths = _control_paths(nodes, graph.control_edges)
    analyzer = _OccurrenceAnalyzer(nodes, paths)
    end_index, _, reaching = analyzer.visit_region(0, (), (), [])
    if end_index != len(nodes) or len(analyzer.consumed_ids) != len(nodes):
        raise USGAnalysisError("USG occurrence traversal did not consume every node once")
    return AnalysisFacts(
        tuple(analyzer.communication_predecessors),
        tuple(analyzer.incoming_communication_frontiers),
        tuple(analyzer.definitions),
        tuple(analyzer.uses),
        tuple(analyzer.reaching_producers),
        tuple(analyzer.uses_without_graph_local_producer),
        tuple(analyzer.value_uses),
        tuple(
            (definition, tuple(consumers))
            for definition, consumers in analyzer.definition_consumers
        ),
        tuple(
            ExitReachingValue(symbol, definitions, entry_value_may_reach)
            for symbol, definitions, entry_value_may_reach in reaching
        ),
    )


def _control_paths(
    nodes: tuple[USGNode, ...], control_edges: tuple[ControlEdge, ...]
) -> dict[int, _ControlPath]:
    node_order = {id(node): index for index, node in enumerate(nodes)}
    incoming: dict[int, list[ControlEdge]] = {id(node): [] for node in nodes}
    for edge in control_edges:
        if not isinstance(edge.source, PredicateNode):
            raise USGAnalysisError("CONTROL edge source must be a PredicateNode")
        if node_order[id(edge.source)] >= node_order[id(edge.target)]:
            raise USGAnalysisError("CONTROL predicate must precede its controlled node")
        incoming[id(edge.target)].append(edge)

    paths: dict[int, _ControlPath] = {}
    for node in nodes:
        edges = sorted(incoming[id(node)], key=lambda edge: node_order[id(edge.source)])
        path: list[tuple[PredicateNode, bool]] = []
        for edge in edges:
            if any(predicate is edge.source for predicate, _ in path):
                raise USGAnalysisError("node has duplicate CONTROL edges from one predicate")
            path.append((edge.source, edge.polarity))
        paths[id(node)] = tuple(path)
    return paths


class _OccurrenceAnalyzer:
    def __init__(self, nodes: tuple[USGNode, ...], paths: dict[int, _ControlPath]) -> None:
        self.nodes = nodes
        self.paths = paths
        self.consumed_ids: set[int] = set()
        self.communication_predecessors: list[
            tuple[CommunicationNode, tuple[CommunicationNode, ...]]
        ] = []
        self.incoming_communication_frontiers: list[
            tuple[USGNode, tuple[CommunicationNode, ...]]
        ] = []
        self.definitions: list[tuple[DefinitionNode, tuple[VariableSymbol, ...]]] = []
        self.uses: list[tuple[USGNode, tuple[SemanticValueSymbol, ...]]] = []
        self.reaching_producers: list[
            tuple[USGNode, VariableSymbol, tuple[DefinitionNode, ...]]
        ] = []
        self.uses_without_graph_local_producer: list[tuple[USGNode, SemanticValueSymbol]] = []
        self.value_uses: list[ValueUse] = []
        self.definition_consumers: list[tuple[DefinitionNode, list[ValueUse]]] = []

    def visit_region(
        self,
        index: int,
        active_path: _ControlPath,
        frontier: tuple[CommunicationNode, ...],
        reaching: _ReachingDefinitions,
    ) -> tuple[int, tuple[CommunicationNode, ...], _ReachingDefinitions]:
        """Consume one structured path region and return its ending frontier."""

        while index < len(self.nodes):
            node = self.nodes[index]
            path = self.paths[id(node)]
            if not _has_prefix(path, active_path):
                return index, frontier, reaching
            if path != active_path:
                raise USGAnalysisError("CONTROL paths do not match structured occurrence order")

            self.incoming_communication_frontiers.append((node, frontier))
            self._consume(node)
            self._record_value_facts(node, reaching)
            if isinstance(node, PredicateNode):
                true_path = active_path + ((node, True),)
                index, true_frontier, true_reaching = self.visit_region(
                    index + 1, true_path, frontier, reaching.copy()
                )

                false_path = active_path + ((node, False),)
                if index < len(self.nodes) and _has_prefix(self.paths[id(self.nodes[index])], false_path):
                    index, false_frontier, false_reaching = self.visit_region(
                        index, false_path, frontier, reaching.copy()
                    )
                    frontier = _union_frontiers(true_frontier, false_frontier)
                    reaching = _merge_reaching_definitions(true_reaching, false_reaching)
                else:
                    frontier = _union_frontiers(frontier, true_frontier)
                    reaching = _merge_reaching_definitions(reaching, true_reaching)
                continue

            if isinstance(node, (ReceiveNode, SendNode)):
                self.communication_predecessors.append((node, frontier))
                frontier = (node,)
            index += 1
        return index, frontier, reaching

    def _consume(self, node: USGNode) -> None:
        if id(node) in self.consumed_ids:
            raise USGAnalysisError("USG occurrence node was consumed more than once")
        self.consumed_ids.add(id(node))

    def _record_value_facts(self, node: USGNode, reaching: _ReachingDefinitions) -> None:
        uses = _node_uses(node)
        if isinstance(node, (AssignNode, PredicateNode, SendNode)):
            self.uses.append((node, uses))
        for symbol in uses:
            if isinstance(symbol, VariableSymbol):
                _ensure_reaching_variable(reaching, symbol)
                producers = _reaching_definitions(reaching, symbol)
                entry_value_may_reach = _entry_value_may_reach(reaching, symbol)
            else:
                producers = ()
                entry_value_may_reach = False
            origin = _value_origin(symbol, producers)
            value_use = ValueUse(
                node, symbol, origin, producers, entry_value_may_reach
            )
            self.value_uses.append(value_use)
            if not producers:
                self.uses_without_graph_local_producer.append((node, symbol))
            if isinstance(symbol, VariableSymbol):
                self.reaching_producers.append((node, symbol, producers))
            for producer in producers:
                self._record_definition_consumer(producer, value_use)

        definitions = _node_definitions(node)
        if isinstance(node, (ReceiveNode, AssignNode)):
            self.definitions.append((node, definitions))
            self.definition_consumers.append((node, []))
        for variable in definitions:
            _set_reaching_definition(reaching, variable, node)

    def _record_definition_consumer(
        self, definition: DefinitionNode, value_use: ValueUse
    ) -> None:
        for known, consumers in self.definition_consumers:
            if known is definition:
                consumers.append(value_use)
                return
        raise USGAnalysisError("reaching definition was not recorded in this graph")


def _has_prefix(path: _ControlPath, prefix: _ControlPath) -> bool:
    return len(path) >= len(prefix) and all(
        predicate is expected_predicate and polarity is expected_polarity
        for (predicate, polarity), (expected_predicate, expected_polarity) in zip(path, prefix)
    )


def _union_frontiers(
    *frontiers: tuple[CommunicationNode, ...],
) -> tuple[CommunicationNode, ...]:
    merged: list[CommunicationNode] = []
    for frontier in frontiers:
        for communication in frontier:
            if not any(communication is existing for existing in merged):
                merged.append(communication)
    return tuple(merged)


def _node_definitions(node: USGNode) -> tuple[VariableSymbol, ...]:
    if isinstance(node, AssignNode):
        assignment = node.semantic_object
        if not isinstance(assignment, AssignmentExpression):
            raise USGAnalysisError("Assign node has no native AssignmentExpression")
        return (_lvalue_variable(assignment.left),)
    if isinstance(node, ReceiveNode):
        call = node.semantic_object
        if not isinstance(call, CallExpression) or len(call.arguments) != 1:
            raise USGAnalysisError("Receive node has no supported native call expression")
        actual = call.arguments[0]
        if not isinstance(actual, AssignmentExpression):
            raise USGAnalysisError("Receive node has no native output assignment")
        return (_lvalue_variable(actual.left),)
    return ()


def _node_uses(node: USGNode) -> tuple[SemanticValueSymbol, ...]:
    if isinstance(node, AssignNode):
        assignment = node.semantic_object
        if not isinstance(assignment, AssignmentExpression):
            raise USGAnalysisError("Assign node has no native AssignmentExpression")
        return _expression_variables(assignment.right)
    if isinstance(node, PredicateNode):
        if not isinstance(node.semantic_object, Expression):
            raise USGAnalysisError("Predicate node has no native Expression")
        return _expression_variables(node.semantic_object)
    if isinstance(node, SendNode):
        call = node.semantic_object
        if not isinstance(call, CallExpression) or len(call.arguments) != 1:
            raise USGAnalysisError("Send node has no supported native call expression")
        return _expression_variables(call.arguments[0])
    return ()


def _lvalue_variable(expression: Expression) -> VariableSymbol:
    if not isinstance(expression, NamedValueExpression):
        raise USGAnalysisError("definition target is not a native NamedValueExpression")
    symbol = expression.getSymbolReference()
    if not isinstance(symbol, VariableSymbol):
        raise USGAnalysisError("definition target is not a native VariableSymbol")
    return symbol


def _expression_variables(expression: Expression) -> tuple[SemanticValueSymbol, ...]:
    values: list[SemanticValueSymbol] = []

    def visit(item: object) -> None:
        if not isinstance(item, NamedValueExpression):
            return
        symbol = item.getSymbolReference()
        if not isinstance(symbol, (VariableSymbol, ParameterSymbol)):
            raise USGAnalysisError("expression reference is not a VariableSymbol or ParameterSymbol")
        if not any(symbol is known for known in values):
            values.append(symbol)

    expression.visit(visit)
    return tuple(values)


def _value_origin(
    symbol: SemanticValueSymbol, producers: tuple[DefinitionNode, ...]
) -> ValueOrigin:
    if producers:
        return ValueOrigin.GRAPH_LOCAL
    if isinstance(symbol, ParameterSymbol):
        return ValueOrigin.PARAMETER
    if isinstance(symbol.syntax.parent, ImplicitAnsiPortSyntax):
        return ValueOrigin.PORT_ENTRY
    return ValueOrigin.LOCAL_ENTRY


def _reaching_definitions(
    reaching: _ReachingDefinitions, variable: VariableSymbol
) -> tuple[DefinitionNode, ...]:
    for known, definitions, _ in reaching:
        if known is variable:
            return definitions
    return ()


def _entry_value_may_reach(
    reaching: _ReachingDefinitions, variable: VariableSymbol
) -> bool:
    for known, _, entry_value_may_reach in reaching:
        if known is variable:
            return entry_value_may_reach
    return True


def _ensure_reaching_variable(
    reaching: _ReachingDefinitions, variable: VariableSymbol
) -> None:
    if any(known is variable for known, _, _ in reaching):
        return
    reaching.append((variable, (), True))


def _set_reaching_definition(
    reaching: _ReachingDefinitions, variable: VariableSymbol, node: DefinitionNode
) -> None:
    for index, (known, _, _) in enumerate(reaching):
        if known is variable:
            reaching[index] = (variable, (node,), False)
            return
    reaching.append((variable, (node,), False))


def _merge_reaching_definitions(*environments: _ReachingDefinitions) -> _ReachingDefinitions:
    merged: list[tuple[VariableSymbol, list[DefinitionNode], bool]] = []
    for environment in environments:
        for variable, definitions, entry_value_may_reach in environment:
            for index, (known, known_definitions, known_entry_value_may_reach) in enumerate(merged):
                if known is variable:
                    for definition in definitions:
                        if not any(definition is existing for existing in known_definitions):
                            known_definitions.append(definition)
                    merged[index] = (
                        known,
                        known_definitions,
                        known_entry_value_may_reach or entry_value_may_reach,
                    )
                    break
            else:
                merged.append((variable, list(definitions), entry_value_may_reach))

    for index, (variable, definitions, entry_value_may_reach) in enumerate(merged):
        if any(not any(known is variable for known, _, _ in environment) for environment in environments):
            entry_value_may_reach = True
        merged[index] = (variable, definitions, entry_value_may_reach)
    return [
        (variable, tuple(definitions), entry_value_may_reach)
        for variable, definitions, entry_value_may_reach in merged
    ]
