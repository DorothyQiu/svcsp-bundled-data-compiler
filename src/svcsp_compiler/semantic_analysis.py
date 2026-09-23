"""M5 semantic dependency, validity, and communication-independence analysis."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from itertools import product

from . import behavioral_ir as behavioral
from .communication_decomposition import BodyReceive, DecomposedTransaction, Enable
from .transaction import RegionOperation, SourcePath


class SemanticDependencyKind(str, Enum):
    DATA = 'data'
    CONTROL = 'control'


@dataclass(frozen=True)
class SemanticDependency:
    source: RegionOperation | Enable
    target: RegionOperation | Enable
    kind: SemanticDependencyKind


@dataclass(frozen=True)
class ReceiveValidity:
    body_receive: BodyReceive
    target: behavioral.Variable | behavioral.Expression
    valid_when: Enable


@dataclass(frozen=True)
class SemanticallyValidatedTransaction:
    decomposed: DecomposedTransaction
    dependencies: tuple[SemanticDependency, ...]
    receive_validity: tuple[ReceiveValidity, ...]


class SemanticValidationError(ValueError):
    """A decomposed transaction cannot be proven semantically valid."""


@dataclass(frozen=True)
class _Literal:
    expression: behavioral.Expression
    positive: bool


Guard = frozenset[_Literal]


@dataclass(frozen=True)
class _Definition:
    source: RegionOperation
    available_when: Guard
    valid_when: Guard


class _Analyzer:
    def __init__(self, decomposed: DecomposedTransaction) -> None:
        self.decomposed = decomposed
        transaction = decomposed.transaction
        self.operations = {
            source.path: source
            for source in transaction.receives + transaction.combinational + transaction.sends
        }
        self.body_receives = {body_receive.source: body_receive for body_receive in decomposed.body_receives}
        self.receive_producers: dict[behavioral.Variable, set[RegionOperation]] = {}
        for body_receive in decomposed.body_receives:
            target = _target_variable(body_receive.source.operation.target)
            if target is not None:
                self.receive_producers.setdefault(target, set()).add(body_receive.source)
        self.enables_by_source = {enable.source: enable for enable in decomposed.enables}
        # External inputs are module-owned declarations.  Membership must use
        # object identity: a local declaration with the same spelling is not
        # an external input.
        self.external_inputs = transaction.behavioral.external_inputs
        self.local_variables = {
            id(variable): variable
            for variable in transaction.behavioral.variables
        }
        self.dependencies: list[SemanticDependency] = []
        self._dependency_keys: set[tuple[object, object, SemanticDependencyKind]] = set()
        self.validity = tuple(
            ReceiveValidity(body_receive, body_receive.source.operation.target, body_receive.valid_when)
            for body_receive in decomposed.body_receives
            if body_receive.valid_when is not None
        )

    def run(self) -> SemanticallyValidatedTransaction:
        self._validate_whole_variable_targets(self.decomposed.transaction.behavioral.body)
        self._validate_concurrent_receive_targets()
        self._process(self.decomposed.transaction.behavioral.body, (), {}, frozenset())
        return SemanticallyValidatedTransaction(self.decomposed, tuple(self.dependencies), self.validity)

    def _validate_whole_variable_targets(self, process: behavioral.Process) -> None:
        if isinstance(process, (behavioral.Receive, behavioral.Assign)):
            if isinstance(process.target, behavioral.Expression) and process.target.form == 'select':
                raise SemanticValidationError('R9A does not support selected lvalue targets')
            return
        if isinstance(process, behavioral.Sequence):
            for item in process.items:
                self._validate_whole_variable_targets(item)
            return
        if isinstance(process, behavioral.Parallel):
            for branch in process.branches:
                self._validate_whole_variable_targets(branch)
            return
        if isinstance(process, behavioral.If):
            self._validate_whole_variable_targets(process.then_branch)
            self._validate_whole_variable_targets(process.else_branch)

    def _validate_concurrent_receive_targets(self) -> None:
        targets: list[behavioral.Variable] = []
        for body_receive in self.decomposed.body_receives:
            target = _target_variable(body_receive.source.operation.target)
            if target is None:
                raise SemanticValidationError('cannot establish Receive target variable')
            if any(target is previous for previous in targets):
                raise SemanticValidationError('concurrent Receive targets overlap')
            targets.append(target)

    def _parallel_accesses(self, process: behavioral.Process) -> dict[int, tuple[behavioral.Variable, set[str]]]:
        """Collect exact local-variable reads and writes in one Parallel branch."""

        accesses: dict[int, tuple[behavioral.Variable, set[str]]] = {}

        def add(variable: behavioral.Variable | None, kind: str) -> None:
            if variable is None:
                return
            local = self.local_variables.get(id(variable))
            if local is not variable:
                return
            entry = accesses.get(id(variable))
            if entry is None:
                accesses[id(variable)] = (variable, {kind})
            else:
                entry[1].add(kind)

        def read_expression(expression: behavioral.Expression) -> None:
            add(expression.variable, 'read')
            for operand in expression.operands:
                read_expression(operand)

        def visit(item: behavioral.Process) -> None:
            if isinstance(item, behavioral.Receive):
                add(_target_variable(item.target), 'write')
                for selector in item.channel.selectors:
                    read_expression(selector)
                return
            if isinstance(item, behavioral.Assign):
                add(_target_variable(item.target), 'write')
                read_expression(item.value)
                return
            if isinstance(item, behavioral.Send):
                read_expression(item.value)
                for selector in item.channel.selectors:
                    read_expression(selector)
                return
            if isinstance(item, behavioral.If):
                read_expression(item.condition)
                visit(item.then_branch)
                visit(item.else_branch)
                return
            if isinstance(item, behavioral.Sequence):
                for child in item.items:
                    visit(child)
                return
            if isinstance(item, behavioral.Parallel):
                for branch in item.branches:
                    visit(branch)

        visit(process)
        return accesses

    def _validate_parallel_noninterference(self, process: behavioral.Parallel) -> None:
        branches = [self._parallel_accesses(branch) for branch in process.branches]
        for index, left in enumerate(branches):
            for right in branches[index + 1:]:
                for variable_id, (variable, left_kinds) in left.items():
                    right_entry = right.get(variable_id)
                    if right_entry is None or right_entry[0] is not variable:
                        continue
                    right_kinds = right_entry[1]
                    if 'write' in left_kinds and ({'read', 'write'} & right_kinds):
                        raise SemanticValidationError(
                            f'Parallel combinational branches conflict on variable {variable.name}'
                        )
                    if 'write' in right_kinds and ({'read', 'write'} & left_kinds):
                        raise SemanticValidationError(
                            f'Parallel combinational branches conflict on variable {variable.name}'
                        )

    def _edge(self, source: RegionOperation | Enable, target: RegionOperation | Enable,
              kind: SemanticDependencyKind) -> None:
        key = (source, target, kind)
        if key not in self._dependency_keys:
            self._dependency_keys.add(key)
            self.dependencies.append(SemanticDependency(source, target, kind))

    def _process(self, process: behavioral.Process, path: SourcePath,
                 definitions: dict[behavioral.Variable, set[_Definition]], guard: Guard
                 ) -> dict[behavioral.Variable, set[_Definition]]:
        if isinstance(process, behavioral.Skip):
            return definitions
        if isinstance(process, behavioral.Sequence):
            current = definitions
            for index, item in enumerate(process.items):
                current = self._process(item, path + (index,), current, guard)
            return current
        if isinstance(process, behavioral.Parallel):
            self._validate_parallel_noninterference(process)
            branches = [self._process(branch, path + ('parallel', index), _copy_definitions(definitions), guard)
                        for index, branch in enumerate(process.branches)]
            return _merge_definitions(branches) if branches else definitions
        if isinstance(process, behavioral.If):
            self._read_expression(process.condition, definitions, guard, None)
            then_definitions = self._process(
                process.then_branch, path + ('then',), _copy_definitions(definitions), _extend(guard, process.condition),
            )
            else_definitions = self._process(
                process.else_branch, path + ('else',), _copy_definitions(definitions), _extend(guard, process.condition, False),
            )
            return _merge_definitions([then_definitions, else_definitions])
        source = self.operations.get(path)
        if source is None:
            raise SemanticValidationError(f'missing M3 region operation at source path {path!r}')
        if isinstance(process, behavioral.Receive):
            enable = self.enables_by_source.get(source)
            if enable is not None:
                self._analyze_enable(enable, definitions)
            target = _target_variable(process.target)
            if target is None:
                raise SemanticValidationError('cannot establish Receive target variable')
            body_receive = self.body_receives.get(source)
            if body_receive is None:
                raise SemanticValidationError('missing M4 BODY receive')
            valid_guard = (_guard_for(body_receive.valid_when.condition)
                           if body_receive.valid_when is not None else guard)
            updated = _copy_definitions(definitions)
            updated[target] = {_Definition(source, guard, valid_guard)}
            return updated
        if isinstance(process, behavioral.Assign):
            self._read_expression(process.value, definitions, guard, source)
            target = _target_variable(process.target)
            if target is None:
                raise SemanticValidationError('cannot establish assignment target variable')
            updated = _copy_definitions(definitions)
            updated[target] = {_Definition(source, guard, guard)}
            return updated
        if isinstance(process, behavioral.Send):
            enable = self.enables_by_source.get(source)
            if enable is not None:
                self._analyze_enable(enable, definitions)
            use_guard = _guard_for(enable.condition) if enable is not None else guard
            self._read_expression(process.value, definitions, use_guard, source)
            if enable is not None:
                self._edge(enable, source, SemanticDependencyKind.CONTROL)
            return definitions
        raise SemanticValidationError(f'unsupported behavioral process {type(process).__name__}')

    def _analyze_enable(self, enable: Enable,
                        definitions: dict[behavioral.Variable, set[_Definition]]) -> None:
        """Analyze every M4 enable at its source occurrence, including composed guards."""

        if isinstance(enable.source.operation, behavioral.Receive):
            for variable in _expression_variables(enable.condition):
                producers = self.receive_producers.get(variable, set())
                if any(producer is not enable.source for producer in producers):
                    raise SemanticValidationError('Receive enable depends on another Receive data')
        self._read_expression(
            enable.condition, definitions, frozenset(), enable,
            receive_enable=isinstance(enable.source.operation, behavioral.Receive),
        )
        self._edge(enable, enable.source, SemanticDependencyKind.CONTROL)

    def _read_expression(self, expression: behavioral.Expression,
                         definitions: dict[behavioral.Variable, set[_Definition]], guard: Guard,
                         target: RegionOperation | Enable | None, *, receive_enable: bool = False) -> None:
        if expression.form == 'conditional' and len(expression.operands) == 3:
            predicate, then_value, else_value = expression.operands
            self._read_expression(predicate, definitions, guard, target, receive_enable=receive_enable)
            self._read_expression(then_value, definitions, _extend(guard, predicate), target,
                                  receive_enable=receive_enable)
            self._read_expression(else_value, definitions, _extend(guard, predicate, False), target,
                                  receive_enable=receive_enable)
            return
        if expression.form == 'binary' and expression.operator in {'&&', '||'} and len(expression.operands) == 2:
            left, right = expression.operands
            self._read_expression(left, definitions, guard, target, receive_enable=receive_enable)
            right_guard = _extend(guard, left, expression.operator == '&&')
            self._read_expression(right, definitions, right_guard, target, receive_enable=receive_enable)
            return
        for variable in _expression_variables(expression):
            reaching = definitions.get(variable, set())
            if not reaching:
                if not any(variable is external for external in self.external_inputs):
                    raise SemanticValidationError(
                        f'variable read has no reaching local definition and is not an explicit external input: '
                        f'{variable.name}'
                    )
                continue
            if receive_enable and any(isinstance(definition.source.operation, (behavioral.Receive, behavioral.Assign))
                                      for definition in reaching):
                raise SemanticValidationError(
                    'Conditional Receive enable may depend only on literals, parameters, and explicit external inputs'
                )
            if not _definitions_cover(reaching, guard):
                raise SemanticValidationError(f'conditional receive data {variable.name} is not valid under this guard')
            if target is not None:
                for definition in reaching:
                    self._edge(definition.source, target, SemanticDependencyKind.DATA)


def analyze_semantics(decomposed: DecomposedTransaction) -> SemanticallyValidatedTransaction:
    """Prove semantic validity of a decomposed single-stage transaction."""

    if not isinstance(decomposed, DecomposedTransaction):
        raise TypeError('expected a DecomposedTransaction')
    return _Analyzer(decomposed).run()


def _target_variable(target: behavioral.Variable | behavioral.Expression) -> behavioral.Variable | None:
    return target if isinstance(target, behavioral.Variable) else target.variable


def _expression_variables(expression: behavioral.Expression) -> set[behavioral.Variable]:
    variables = {expression.variable} if expression.variable is not None else set()
    for operand in expression.operands:
        variables |= _expression_variables(operand)
    return variables


def _extend(guard: Guard, expression: behavioral.Expression, positive: bool = True) -> Guard:
    if expression.form == 'binary' and expression.operator == '&&' and positive:
        return _extend(_extend(guard, expression.operands[0]), expression.operands[1])
    if expression.form == 'unary' and expression.operator == '!':
        return _extend(guard, expression.operands[0], not positive)
    return guard | {_Literal(expression, positive)}


def _guard_for(expression: behavioral.Expression) -> Guard:
    return _extend(frozenset(), expression)


def _copy_definitions(definitions: dict[behavioral.Variable, set[_Definition]]) -> dict[behavioral.Variable, set[_Definition]]:
    return {variable: set(values) for variable, values in definitions.items()}


def _merge_definitions(flows: list[dict[behavioral.Variable, set[_Definition]]]
                       ) -> dict[behavioral.Variable, set[_Definition]]:
    merged: dict[behavioral.Variable, set[_Definition]] = {}
    for definitions in flows:
        for variable, values in definitions.items():
            merged.setdefault(variable, set()).update(values)
    return merged


def _definitions_cover(definitions: set[_Definition], current_guard: Guard) -> bool:
    """Check that every path admitted by ``current_guard`` has one valid definition."""

    atoms = {literal.expression for literal in current_guard}
    for definition in definitions:
        atoms.update(literal.expression for literal in definition.available_when)
        atoms.update(literal.expression for literal in definition.valid_when)
    atoms = tuple(atoms)
    for values in product((False, True), repeat=len(atoms)):
        valuation = dict(zip(atoms, values))
        if not _satisfies(current_guard, valuation):
            continue
        if not any(_satisfies(definition.available_when, valuation) and
                   _satisfies(definition.valid_when, valuation) for definition in definitions):
            return False
    return True


def _satisfies(guard: Guard, valuation: dict[behavioral.Expression, bool]) -> bool:
    return all(valuation[literal.expression] is literal.positive for literal in guard)
