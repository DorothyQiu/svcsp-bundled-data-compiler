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


@dataclass(frozen=True)
class _LValue:
    """An exact local Variable and the concrete bits written or read.

    ``bits`` is ``None`` only for an unselected symbolic-width whole Variable;
    concrete Variables always use an inclusive, normalized bit interval.
    """

    variable: behavioral.Variable
    bits: tuple[int, int] | None


@dataclass(frozen=True)
class _Access:
    lvalue: _LValue
    kind: str


DefinitionState = dict[
    int,
    tuple[behavioral.Variable, dict[int | None, set[_Definition]]],
]


class _Analyzer:
    def __init__(self, decomposed: DecomposedTransaction) -> None:
        self.decomposed = decomposed
        transaction = decomposed.transaction
        self.operations = {
            source.path: source
            for source in transaction.receives + transaction.combinational + transaction.sends
        }
        # External inputs are module-owned declarations.  Membership must use
        # object identity: a local declaration with the same spelling is not
        # an external input.
        self.external_inputs = transaction.behavioral.external_inputs
        self.local_variables = {
            id(variable): variable
            for variable in transaction.behavioral.variables
        }
        self.body_receives = {body_receive.source: body_receive for body_receive in decomposed.body_receives}
        self.receive_producers: list[tuple[_LValue, RegionOperation]] = []
        for body_receive in decomposed.body_receives:
            self.receive_producers.append((
                self._target_lvalue(body_receive.source.operation.target),
                body_receive.source,
            ))
        self.enables_by_source = {enable.source: enable for enable in decomposed.enables}
        self.dependencies: list[SemanticDependency] = []
        self._dependency_keys: set[tuple[object, object, SemanticDependencyKind]] = set()
        self.validity = tuple(
            ReceiveValidity(body_receive, body_receive.source.operation.target, body_receive.valid_when)
            for body_receive in decomposed.body_receives
            if body_receive.valid_when is not None
        )

    def run(self) -> SemanticallyValidatedTransaction:
        self._validate_lvalue_targets(self.decomposed.transaction.behavioral.body)
        self._validate_concurrent_receive_targets()
        self._process(self.decomposed.transaction.behavioral.body, (), {}, frozenset())
        return SemanticallyValidatedTransaction(self.decomposed, tuple(self.dependencies), self.validity)

    def _validate_lvalue_targets(self, process: behavioral.Process) -> None:
        if isinstance(process, (behavioral.Receive, behavioral.Assign)):
            self._target_lvalue(process.target)
            return
        if isinstance(process, behavioral.Sequence):
            for item in process.items:
                self._validate_lvalue_targets(item)
            return
        if isinstance(process, behavioral.Parallel):
            for branch in process.branches:
                self._validate_lvalue_targets(branch)
            return
        if isinstance(process, behavioral.If):
            self._validate_lvalue_targets(process.then_branch)
            self._validate_lvalue_targets(process.else_branch)

    def _target_lvalue(
        self,
        target: behavioral.Variable | behavioral.Expression,
    ) -> _LValue:
        if isinstance(target, behavioral.Variable):
            if self.local_variables.get(id(target)) is not target:
                raise SemanticValidationError('R9A BODY write target must be an exact local Variable')
            return _whole_lvalue(target)
        if target.form != 'select' or target.variable is None:
            raise SemanticValidationError('R9B lvalue target must be a local Variable or static selection')
        variable = target.variable
        if self.local_variables.get(id(variable)) is not variable:
            raise SemanticValidationError('R9A BODY write target must be an exact local Variable')
        width = variable.payload_type.width.bits
        if width is None:
            raise SemanticValidationError('R9B does not support selected lvalues on symbolic-width Variables')
        if len(target.operands) != 1:
            raise SemanticValidationError('R9B lvalue selector must be one static literal index or range')
        selector = target.operands[0]
        if selector.form == 'index' and len(selector.operands) == 1:
            index = _literal_integer(selector.operands[0])
            if index is None:
                raise SemanticValidationError('R9B lvalue index must be a literal integer')
            interval = (index, index)
        elif selector.form == 'range' and len(selector.operands) == 2:
            left = _literal_integer(selector.operands[0])
            right = _literal_integer(selector.operands[1])
            if left is None or right is None:
                raise SemanticValidationError('R9B lvalue range endpoints must be literal integers')
            interval = (min(left, right), max(left, right))
        else:
            raise SemanticValidationError('R9B lvalue selector must be one static literal index or range')
        if interval[0] < 0 or interval[1] >= width:
            raise SemanticValidationError('R9B lvalue selection is out of bounds')
        return _LValue(variable, interval)

    def _validate_concurrent_receive_targets(self) -> None:
        targets: list[_LValue] = []
        for body_receive in self.decomposed.body_receives:
            target = self._target_lvalue(body_receive.source.operation.target)
            if any(_overlap(target, previous) for previous in targets):
                raise SemanticValidationError('concurrent Receive targets overlap')
            targets.append(target)

    def _parallel_accesses(self, process: behavioral.Process) -> list[_Access]:
        """Collect exact local-variable reads and writes in one Parallel branch."""

        accesses: list[_Access] = []

        def add(lvalue: _LValue | None, kind: str) -> None:
            if lvalue is None:
                return
            local = self.local_variables.get(id(lvalue.variable))
            if local is not lvalue.variable:
                return
            accesses.append(_Access(lvalue, kind))

        def read_expression(expression: behavioral.Expression) -> None:
            for lvalue in self._expression_lvalues(expression):
                add(lvalue, 'read')

        def visit(item: behavioral.Process) -> None:
            if isinstance(item, behavioral.Receive):
                add(self._target_lvalue(item.target), 'write')
                for selector in item.channel.selectors:
                    read_expression(selector)
                return
            if isinstance(item, behavioral.Assign):
                add(self._target_lvalue(item.target), 'write')
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
                for left_access in left:
                    for right_access in right:
                        if not _overlap(left_access.lvalue, right_access.lvalue):
                            continue
                        if left_access.kind == 'read' and right_access.kind == 'read':
                            continue
                        variable = left_access.lvalue.variable
                        raise SemanticValidationError(
                            f'Parallel combinational branches conflict on variable {variable.name}'
                        )

    def _expression_lvalues(self, expression: behavioral.Expression) -> tuple[_LValue, ...]:
        """Return exact local read coverage; dynamic rvalue selects read all bits."""

        accesses: list[_LValue] = []

        def add(variable: behavioral.Variable | None, bits: tuple[int, int] | None) -> None:
            if variable is not None:
                accesses.append(_LValue(variable, bits))

        def visit(item: behavioral.Expression) -> None:
            if item.form == 'select' and item.variable is not None:
                interval = _static_rvalue_interval(item)
                add(item.variable, interval if interval is not None else _whole_lvalue(item.variable).bits)
                # Selector operands may themselves read Variables even though
                # a dynamic selector conservatively reads the full base.
                for selector in item.operands:
                    for operand in selector.operands:
                        visit(operand)
                return
            add(item.variable, _whole_lvalue(item.variable).bits if item.variable is not None else None)
            for operand in item.operands:
                visit(operand)

        visit(expression)
        return tuple(accesses)

    def _edge(self, source: RegionOperation | Enable, target: RegionOperation | Enable,
              kind: SemanticDependencyKind) -> None:
        key = (source, target, kind)
        if key not in self._dependency_keys:
            self._dependency_keys.add(key)
            self.dependencies.append(SemanticDependency(source, target, kind))

    def _process(self, process: behavioral.Process, path: SourcePath,
                 definitions: DefinitionState, guard: Guard
                 ) -> DefinitionState:
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
            return _merge_parallel_definitions(definitions, branches) if branches else definitions
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
            target = self._target_lvalue(process.target)
            body_receive = self.body_receives.get(source)
            if body_receive is None:
                raise SemanticValidationError('missing M4 BODY receive')
            valid_guard = (_guard_for(body_receive.valid_when.condition)
                           if body_receive.valid_when is not None else guard)
            updated = _copy_definitions(definitions)
            _write_definition(updated, target, _Definition(source, guard, valid_guard))
            return updated
        if isinstance(process, behavioral.Assign):
            self._read_expression(process.value, definitions, guard, source)
            target = self._target_lvalue(process.target)
            updated = _copy_definitions(definitions)
            _write_definition(updated, target, _Definition(source, guard, guard))
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
                        definitions: DefinitionState) -> None:
        """Analyze every M4 enable at its source occurrence, including composed guards."""

        if isinstance(enable.source.operation, behavioral.Receive):
            for lvalue in self._expression_lvalues(enable.condition):
                if any(_overlap(lvalue, producer) and source is not enable.source
                       for producer, source in self.receive_producers):
                    raise SemanticValidationError('Receive enable depends on another Receive data')
        self._read_expression(
            enable.condition, definitions, frozenset(), enable,
            receive_enable=isinstance(enable.source.operation, behavioral.Receive),
        )
        self._edge(enable, enable.source, SemanticDependencyKind.CONTROL)

    def _read_expression(self, expression: behavioral.Expression,
                         definitions: DefinitionState, guard: Guard,
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
        for access in self._expression_lvalues(expression):
            variable = access.variable
            entry = definitions.get(id(variable))
            if entry is None or entry[0] is not variable:
                if any(variable is external for external in self.external_inputs):
                    continue
                raise SemanticValidationError(
                    f'variable read has no reaching local definition and is not an explicit external input: '
                    f'{variable.name}'
                )
            for bit in _bits(access):
                reaching = entry[1].get(bit, set())
                if receive_enable and any(isinstance(definition.source.operation, (behavioral.Receive, behavioral.Assign))
                                          for definition in reaching):
                    raise SemanticValidationError(
                        'Conditional Receive enable may depend only on literals, parameters, and explicit external inputs'
                    )
                if not _definitions_cover(reaching, guard):
                    raise SemanticValidationError(
                        f'conditional receive data {variable.name} is not valid under this guard'
                    )
                if target is not None:
                    for definition in reaching:
                        self._edge(definition.source, target, SemanticDependencyKind.DATA)


def analyze_semantics(decomposed: DecomposedTransaction) -> SemanticallyValidatedTransaction:
    """Prove semantic validity of a decomposed single-stage transaction."""

    if not isinstance(decomposed, DecomposedTransaction):
        raise TypeError('expected a DecomposedTransaction')
    return _Analyzer(decomposed).run()


def _whole_lvalue(variable: behavioral.Variable) -> _LValue:
    width = variable.payload_type.width.bits
    return _LValue(variable, None if width is None else (0, width - 1))


def _literal_integer(expression: behavioral.Expression) -> int | None:
    if expression.form != 'literal' or expression.value is None:
        return None
    text = expression.value.replace('_', '')
    try:
        if "'" in text:
            _, value = text.split("'", 1)
            if not value or value[0].lower() not in {'d', 'h', 'o', 'b'}:
                return None
            base = {'d': 10, 'h': 16, 'o': 8, 'b': 2}[value[0].lower()]
            return int(value[1:], base)
        return int(text, 10)
    except ValueError:
        return None


def _static_rvalue_interval(expression: behavioral.Expression) -> tuple[int, int] | None:
    """Return a static in-bounds interval, or conservatively read all bits."""

    if expression.form != 'select' or expression.variable is None:
        return None
    width = expression.variable.payload_type.width.bits
    if width is None or len(expression.operands) != 1:
        return None
    selector = expression.operands[0]
    if selector.form == 'index' and len(selector.operands) == 1:
        index = _literal_integer(selector.operands[0])
        interval = None if index is None else (index, index)
    elif selector.form == 'range' and len(selector.operands) == 2:
        left = _literal_integer(selector.operands[0])
        right = _literal_integer(selector.operands[1])
        interval = None if left is None or right is None else (min(left, right), max(left, right))
    else:
        return None
    if interval is None or interval[0] < 0 or interval[1] >= width:
        return None
    return interval


def _overlap(left: _LValue, right: _LValue) -> bool:
    if left.variable is not right.variable:
        return False
    if left.bits is None or right.bits is None:
        return True
    return left.bits[0] <= right.bits[1] and right.bits[0] <= left.bits[1]


def _bits(lvalue: _LValue) -> tuple[int | None, ...]:
    if lvalue.bits is None:
        return (None,)
    return tuple(range(lvalue.bits[0], lvalue.bits[1] + 1))


def _extend(guard: Guard, expression: behavioral.Expression, positive: bool = True) -> Guard:
    if expression.form == 'binary' and expression.operator == '&&' and positive:
        return _extend(_extend(guard, expression.operands[0]), expression.operands[1])
    if expression.form == 'unary' and expression.operator == '!':
        return _extend(guard, expression.operands[0], not positive)
    return guard | {_Literal(expression, positive)}


def _guard_for(expression: behavioral.Expression) -> Guard:
    return _extend(frozenset(), expression)


def _copy_definitions(definitions: DefinitionState) -> DefinitionState:
    return {
        variable_id: (variable, {bit: set(values) for bit, values in coverage.items()})
        for variable_id, (variable, coverage) in definitions.items()
    }


def _write_definition(
    definitions: DefinitionState,
    target: _LValue,
    definition: _Definition,
) -> None:
    variable_id = id(target.variable)
    entry = definitions.get(variable_id)
    if entry is None:
        coverage: dict[int | None, set[_Definition]] = {}
        definitions[variable_id] = (target.variable, coverage)
    elif entry[0] is target.variable:
        coverage = entry[1]
    else:
        raise SemanticValidationError('definition identity collision')
    for bit in _bits(target):
        coverage[bit] = {definition}


def _merge_definitions(flows: list[DefinitionState]) -> DefinitionState:
    merged: DefinitionState = {}
    for definitions in flows:
        for variable_id, (variable, coverage) in definitions.items():
            entry = merged.get(variable_id)
            if entry is None:
                target_coverage: dict[int | None, set[_Definition]] = {}
                merged[variable_id] = (variable, target_coverage)
            elif entry[0] is variable:
                target_coverage = entry[1]
            else:
                raise SemanticValidationError('definition identity collision')
            for bit, values in coverage.items():
                target_coverage.setdefault(bit, set()).update(values)
    return merged


def _merge_parallel_definitions(initial: DefinitionState, flows: list[DefinitionState]) -> DefinitionState:
    """Merge noninterfering Parallel branches without retaining stale slices.

    Every branch begins with ``initial``.  For any bit changed by one proven
    noninterfering branch, that branch's coverage replaces the inherited bit
    coverage; untouched bits retain their inherited coverage.
    """

    result = _copy_definitions(initial)
    variable_ids = set(initial)
    for flow in flows:
        variable_ids.update(flow)
    for variable_id in variable_ids:
        initial_entry = initial.get(variable_id)
        entries = [flow.get(variable_id) for flow in flows]
        variable = (
            initial_entry[0]
            if initial_entry is not None
            else next(entry[0] for entry in entries if entry is not None)
        )
        if any(entry is not None and entry[0] is not variable for entry in entries):
            raise SemanticValidationError('definition identity collision')
        initial_coverage = initial_entry[1] if initial_entry is not None else {}
        bits = set(initial_coverage)
        for entry in entries:
            if entry is not None:
                bits.update(entry[1])
        coverage = result.setdefault(variable_id, (variable, {}))[1]
        for bit in bits:
            prior = initial_coverage.get(bit, set())
            changed = [
                entry[1].get(bit, set())
                for entry in entries
                if entry is not None and not _same_definitions(entry[1].get(bit, set()), prior)
            ]
            if len(changed) > 1:
                raise SemanticValidationError('Parallel combinational branches overlap')
            coverage[bit] = set(changed[0] if changed else prior)
    return result


def _same_definitions(left: set[_Definition], right: set[_Definition]) -> bool:
    return len(left) == len(right) and all(any(item is other for other in right) for item in left)


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
