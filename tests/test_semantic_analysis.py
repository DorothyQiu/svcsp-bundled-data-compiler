"""M5 semantic dependency and conditional-validity contract tests."""
from __future__ import annotations

from dataclasses import replace

import pytest

from svcsp_compiler.behavioral_ir import (
    Assign,
    BehavioralModule,
    ChannelEndpoint,
    Expression,
    If,
    ONE_BIT,
    Parameter,
    Parallel,
    PayloadType,
    Receive,
    Send,
    Sequence,
    Skip,
    SourceLocation,
    Variable,
    lower_behavioral,
)
from svcsp_compiler.communication_decomposition import decompose_transaction
from svcsp_compiler.frontend import parse_text
from svcsp_compiler.semantic_analysis import (
    ReceiveValidity,
    SemanticDependencyKind,
    SemanticValidationError,
    SemanticallyValidatedTransaction,
    analyze_semantics,
)
from svcsp_compiler.transaction import RegionOperation, StructurallyValidatedTransaction, extract_transaction


_TYPE = PayloadType("logic", ONE_BIT)


class _Program:
    def __init__(self) -> None:
        self.variables: dict[str, Variable] = {}
        self.external_inputs: dict[str, Variable] = {}

    def variable(self, name: str) -> Variable:
        return self.variables.setdefault(
            name,
            Variable(
                name,
                (),
                SourceLocation("semantic.sv", 1, 1),
                _TYPE,
            ),
        )

    def external_input(self, name: str) -> Variable:
        return self.external_inputs.setdefault(
            name,
            Variable(
                name,
                ("external_input",),
                SourceLocation("semantic.sv", 1, 1),
                _TYPE,
            ),
        )

    def name(self, name: str) -> Expression:
        variable = self.variable(name)
        return Expression("name", value=name, variable=variable)

    def condition(self, name: str) -> Expression:
        variable = self.external_input(name)
        return Expression("name", value=name, variable=variable)

    def receive(self, channel: str, target: str) -> Receive:
        return Receive(ChannelEndpoint(channel), self.variable(target))

    def send(self, channel: str, value: Expression) -> Send:
        return Send(ChannelEndpoint(channel), value)

    def assign(self, target: str, value: Expression) -> Assign:
        return Assign(self.variable(target), value)

    def module(self, body) -> BehavioralModule:
        return BehavioralModule(
            "semantic",
            body,
            (),
            tuple(self.variables.values()),
            external_inputs=tuple(self.external_inputs.values()),
        )

def _binary(operator: str, left: Expression, right: Expression) -> Expression:
    return Expression("binary", operator=operator, operands=(left, right))


def _select_bit(expression: Expression) -> Expression:
    return Expression(
        "select", value=expression.value, variable=expression.variable,
        operands=(Expression("index", operands=(Expression("literal", value="0"),)),),
    )


def _decompose(program: _Program, body):
    return decompose_transaction(extract_transaction(program.module(body)))


def _analyze(program: _Program, body) -> SemanticallyValidatedTransaction:
    return analyze_semantics(_decompose(program, body))


def _analyze_source(source: str) -> SemanticallyValidatedTransaction:
    return analyze_semantics(decompose_transaction(extract_transaction(lower_behavioral(parse_text(source)))))


def test_independent_sequential_receives_have_dataflow_without_receive_ordering() -> None:
    program = _Program()
    receive_a, receive_b = program.receive("A", "a"), program.receive("B", "b")
    assignment = program.assign("c", _binary("+", program.name("a"), program.name("b")))
    send_c = program.send("C", program.name("c"))
    decomposed = _decompose(program, Sequence((receive_a, receive_b, assignment, send_c)))
    validated = analyze_semantics(decomposed)

    assert isinstance(validated, SemanticallyValidatedTransaction)
    assert validated.decomposed is decomposed
    dependencies = {(edge.source, edge.target, edge.kind) for edge in validated.dependencies}
    assert (decomposed.transaction.receives[0], decomposed.transaction.combinational[0], SemanticDependencyKind.DATA) in dependencies
    assert (decomposed.transaction.receives[1], decomposed.transaction.combinational[0], SemanticDependencyKind.DATA) in dependencies
    assert (decomposed.transaction.combinational[0], decomposed.transaction.sends[0], SemanticDependencyKind.DATA) in dependencies
    assert not any(edge.source is decomposed.transaction.receives[0] and edge.target is decomposed.transaction.receives[1]
                   for edge in validated.dependencies)


def test_conditional_receive_use_under_same_or_stronger_guard_is_valid() -> None:
    program = _Program()
    select, extra = program.condition("sel"), program.condition("extra")
    conditional_receive = program.receive("A", "a")
    use_when_extra = program.assign("y", program.name("a"))
    use_without_extra = program.assign("y", program.name("a"))
    fallback = program.assign("y", Expression("literal", value="1'b0"))
    body = Sequence((
        If(select, conditional_receive, Skip()),
        If(select, If(extra, use_when_extra, use_without_extra), fallback),
        program.send("B", program.name("y")),
    ))

    validated = _analyze(program, body)

    assert any(isinstance(fact, ReceiveValidity) and fact.body_receive.source.operation is conditional_receive
               for fact in validated.receive_validity)


@pytest.mark.parametrize("unrelated", [False, True], ids=("unguarded", "unrelated-guard"))
def test_conditional_receive_use_requires_proven_guard(unrelated: bool) -> None:
    program = _Program()
    select = program.condition("sel")
    conditional_receive = program.receive("A", "a")
    use = program.assign("y", program.name("a"))
    use_site = If(program.condition("other"), use, program.assign("y", Expression("literal", value="1'b0"))) if unrelated else use
    body = Sequence((
        If(select, conditional_receive, Skip()),
        use_site,
        program.send("B", program.name("y")),
    ))

    with pytest.raises(SemanticValidationError, match="valid"):
        _analyze(program, body)


def test_receive_enable_cannot_depend_on_another_receive_payload() -> None:
    program = _Program()
    first = program.receive("A", "a")
    dependent = program.receive("B", "b")
    body = Sequence((
        first,
        If(_select_bit(program.name("a")), dependent, Skip()),
        program.send("C", Expression("literal", value="1'b0")),
    ))

    with pytest.raises(SemanticValidationError, match="Receive"):
        _analyze(program, body)


def test_if_else_definitions_cover_an_unguarded_later_use() -> None:
    program = _Program()
    body = Sequence((
        program.receive("A", "a"),
        If(program.condition("sel"),
           program.assign("y", program.name("a")),
           program.assign("y", Expression("literal", value="1'b0"))),
        program.send("B", program.name("y")),
    ))

    _analyze(program, body)


def test_conditional_input_alternatives_support_guarded_ternary_arms() -> None:
    program = _Program()
    select = program.condition("sel")
    body = Sequence((
        If(select, program.receive("A", "a"), program.receive("B", "b")),
        program.assign("y", Expression("conditional", operands=(select, program.name("a"), program.name("b")))),
        program.send("C", program.name("y")),
    ))

    _analyze(program, body)


def test_conditional_send_payload_is_checked_under_its_send_enable() -> None:
    program = _Program()
    select = program.condition("sel")
    conditional_receive = program.receive("A", "a")
    conditional_send = program.send("B", program.name("a"))
    body = Sequence((
        If(select, conditional_receive, Skip()),
        If(select, conditional_send, Skip()),
    ))

    validated = _analyze(program, body)

    assert validated.decomposed.en_sends[0].source.operation is conditional_send


def test_enable_condition_data_dependencies_are_recorded() -> None:
    program = _Program()
    receive = program.receive("A", "a")
    conditional_send = program.send("B", Expression("literal", value="1'b0"))
    decomposed = _decompose(program, Sequence((
        receive,
        If(_select_bit(program.name("a")), conditional_send, Skip()),
    )))
    validated = analyze_semantics(decomposed)

    assert any(edge.source is decomposed.transaction.receives[0] and
               edge.target is decomposed.enables[0] and
               edge.kind is SemanticDependencyKind.DATA
               for edge in validated.dependencies)


def test_analysis_preserves_exact_decomposed_transaction_and_m4_identities() -> None:
    program = _Program()
    condition = program.condition("sel")
    conditional_receive = program.receive("A", "a")
    decomposed = _decompose(program, Sequence((
        If(condition, conditional_receive, Skip()),
        program.send("B", Expression("literal", value="1'b0")),
    )))

    validated = analyze_semantics(decomposed)

    fact = validated.receive_validity[0]
    assert validated.decomposed is decomposed
    assert fact.body_receive is decomposed.body_receives[0]
    assert fact.valid_when is decomposed.enables[0]
    assert fact.target is conditional_receive.target


def test_nested_en_recv_enable_cannot_depend_on_receive_data() -> None:
    program = _Program()
    dependent_receive = program.receive("B", "b")
    body = Sequence((
        program.receive("A", "a"),
        If(program.condition("c"), If(_select_bit(program.name("a")), dependent_receive, Skip()), Skip()),
        program.send("C", Expression("literal", value="1'b0")),
    ))

    with pytest.raises(SemanticValidationError, match="Receive"):
        _analyze(program, body)


def test_conditional_receive_data_cannot_drive_later_if_predicate_outside_guard() -> None:
    program = _Program()
    body = Sequence((
        If(program.condition("sel"), program.receive("A", "a"), Skip()),
        If(_select_bit(program.name("a")),
           program.assign("y", Expression("literal", value="1'b1")),
           program.assign("y", Expression("literal", value="1'b0"))),
        program.send("B", program.name("y")),
    ))

    with pytest.raises(SemanticValidationError, match="valid"):
        _analyze(program, body)


def test_conditional_send_accepts_receive_payload_under_composed_stronger_guard() -> None:
    program = _Program()
    select = program.condition("sel")
    conditional_send = program.send("B", program.name("a"))
    body = Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(_binary("&&", select, _select_bit(program.name("a"))), conditional_send, Skip()),
    ))

    _analyze(program, body)


def test_composed_enable_records_receive_data_dependency() -> None:
    program = _Program()
    conditional_send = program.send("B", Expression("literal", value="1'b0"))
    decomposed = _decompose(program, Sequence((
        program.receive("A", "a"),
        If(program.condition("c"),
           If(_select_bit(program.name("a")), conditional_send, Skip()),
           Skip()),
    )))

    validated = analyze_semantics(decomposed)

    assert any(edge.source is decomposed.transaction.receives[0] and
               edge.target is decomposed.enables[0] and
               edge.kind is SemanticDependencyKind.DATA
               for edge in validated.dependencies)


def test_logical_and_rhs_reads_conditional_receive_only_when_lhs_is_true() -> None:
    program = _Program()
    select = program.condition("sel")
    body = Sequence((
        If(select, program.receive("A", "a"), Skip()),
        If(_binary("&&", select, _select_bit(program.name("a"))),
           program.send("B", Expression("literal", value="1'b0")), Skip()),
    ))

    _analyze(program, body)


def test_logical_or_rhs_reads_conditional_receive_only_when_lhs_is_false() -> None:
    program = _Program()
    select = program.condition("sel")
    not_select = Expression("unary", operator="!", operands=(select,))
    body = Sequence((
        If(not_select, program.receive("A", "a"), Skip()),
        If(_binary("||", select, _select_bit(program.name("a"))),
           program.send("B", Expression("literal", value="1'b0")), Skip()),
    ))

    _analyze(program, body)


def test_parallel_receive_enable_cannot_depend_on_another_receive_payload() -> None:
    program = _Program()
    dependent_receive = program.receive("B", "b")
    body = Sequence((
        Parallel((
            program.receive("A", "a"),
            If(_select_bit(program.name("a")), dependent_receive, Skip()),
        )),
        program.send("C", Expression("literal", value="1'b0")),
    ))

    with pytest.raises(SemanticValidationError, match="Parallel combinational branches conflict"):
        _analyze(program, body)


def test_external_input_pre_input_guard_is_accepted() -> None:
    program = _Program()
    body = Sequence((
        If(program.condition("select"), program.receive("A", "a"), Skip()),
        program.send("B", Expression("literal", value="1'b0")),
    ))

    _analyze(program, body)


def test_pre_input_guard_expression_with_multiple_external_inputs_is_accepted() -> None:
    program = _Program()
    body = Sequence((
        If(_binary("&&", program.condition("select"), program.condition("enable")),
           program.receive("A", "a"), Skip()),
        program.send("B", Expression("literal", value="1'b0")),
    ))

    _analyze(program, body)


def test_source_receive_into_external_input_is_rejected() -> None:
    behavioral = lower_behavioral(parse_text('''module m(input logic sel, Channel #(1) A, B);
logic x;
always begin A.Receive(x); B.Send(1'b0); end
endmodule'''))
    receive, send = behavioral.body.items
    malformed = replace(
        behavioral,
        body=Sequence((replace(receive, target=behavioral.external_inputs[0]), send)),
    )
    with pytest.raises(SemanticValidationError, match="exact local Variable"):
        analyze_semantics(decompose_transaction(extract_transaction(malformed)))


def test_source_assign_to_external_input_is_rejected() -> None:
    behavioral = lower_behavioral(parse_text('''module m(input logic sel, Channel #(1) A, B);
logic a, x;
always begin A.Receive(a); x = a; B.Send(x); end
endmodule'''))
    receive, assign, send = behavioral.body.items
    malformed = replace(
        behavioral,
        body=Sequence((receive, replace(assign, target=behavioral.external_inputs[0]), send)),
    )
    with pytest.raises(SemanticValidationError, match="exact local Variable"):
        analyze_semantics(decompose_transaction(extract_transaction(malformed)))


def test_external_input_remains_legal_as_assign_rhs_and_send_value() -> None:
    program = _Program()
    select = program.condition("sel")
    rhs_body = Sequence((
        program.receive("A", "a"),
        program.assign("y", select),
        program.send("B", program.name("y")),
    ))
    send_body = Sequence((
        program.receive("A", "a"),
        program.send("B", select),
    ))

    _analyze(program, rhs_body)
    _analyze(program, send_body)


def test_undefined_local_pre_input_guard_is_rejected() -> None:
    program = _Program()
    body = Sequence((
        If(program.name("undefined"), program.receive("A", "a"), Skip()),
        program.send("B", Expression("literal", value="1'b0")),
    ))

    with pytest.raises(SemanticValidationError, match="no reaching local definition"):
        _analyze(program, body)


def test_receive_data_dependent_conditional_receive_is_rejected() -> None:
    program = _Program()
    body = Sequence((
        program.receive("A", "a"),
        If(_select_bit(program.name("a")), program.receive("B", "b"), Skip()),
        program.send("C", Expression("literal", value="1'b0")),
    ))

    with pytest.raises(SemanticValidationError, match="Receive enable"):
        _analyze(program, body)


def test_assign_result_dependent_conditional_receive_is_rejected() -> None:
    program = _Program()
    assignment = program.assign("control", Expression("literal", value="1'b1"))
    conditional_receive = program.receive("A", "a")
    send = program.send("B", Expression("literal", value="1'b0"))
    body = Sequence((
        assignment,
        If(program.name("control"), conditional_receive, Skip()),
        send,
    ))
    # M3 normally rejects this source ordering.  Construct the M3 input
    # directly so this test exercises M5's fail-closed PRE_INPUT check.
    transaction = StructurallyValidatedTransaction(
        program.module(body),
        (RegionOperation(conditional_receive, (1, "then")),),
        (RegionOperation(assignment, (0,)),),
        (RegionOperation(send, (2,)),),
        (),
    )

    with pytest.raises(SemanticValidationError, match="Conditional Receive enable"):
        analyze_semantics(decompose_transaction(transaction))


def test_unconditional_receive_data_conditional_send_guard_is_accepted() -> None:
    program = _Program()
    body = Sequence((
        program.receive("A", "a"),
        If(_select_bit(program.name("a")),
           program.send("B", Expression("literal", value="1'b0")), Skip()),
    ))

    _analyze(program, body)


def test_locally_computed_conditional_send_guard_is_accepted() -> None:
    program = _Program()
    body = Sequence((
        program.receive("A", "a"),
        program.assign("control", _select_bit(program.name("a"))),
        If(program.name("control"), program.send("B", Expression("literal", value="1'b0")), Skip()),
    ))

    _analyze(program, body)


def test_parameter_pre_input_guard_remains_legal() -> None:
    program = _Program()
    parameter = Parameter("SELECT", "semantic", SourceLocation("semantic.sv", 1, 1))
    parameter_guard = Expression("parameter", value="SELECT", parameter=parameter)
    body = Sequence((
        If(parameter_guard, program.receive("A", "a"), Skip()),
        program.send("B", Expression("literal", value="1'b0")),
    ))
    module = BehavioralModule(
        "semantic", body, (), tuple(program.variables.values()), parameters=(parameter,),
    )

    analyze_semantics(decompose_transaction(extract_transaction(module)))


def test_same_name_local_variable_is_not_an_external_input_by_identity() -> None:
    program = _Program()
    external = Variable("select", (), SourceLocation("semantic.sv", 1, 1), _TYPE)
    local = Variable("select", ("local",), SourceLocation("semantic.sv", 1, 1), _TYPE)
    local_guard = Expression("name", value="select", variable=local)
    body = Sequence((
        If(local_guard, program.receive("A", "a"), Skip()),
        program.send("B", Expression("literal", value="1'b0")),
    ))
    module = BehavioralModule(
        "semantic", body, (), tuple(program.variables.values()) + (local,), external_inputs=(external,),
    )

    with pytest.raises(SemanticValidationError, match="no reaching local definition"):
        analyze_semantics(decompose_transaction(extract_transaction(module)))


def test_same_target_concurrent_receives_are_rejected() -> None:
    with pytest.raises(SemanticValidationError, match="concurrent Receive targets overlap"):
        _analyze_source('''module m(interface A, B, C); logic x; always begin
            A.Receive(x); B.Receive(x); C.Send(x);
        end endmodule''')


def test_distinct_concurrent_receives_remain_legal() -> None:
    program = _Program()
    body = Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        program.send("C", program.name("a")),
    ))

    _analyze(program, body)


def test_parallel_whole_variable_write_write_conflict_is_rejected() -> None:
    program = _Program()
    body = Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        Parallel((
            program.assign("y", program.name("a")),
            program.assign("y", program.name("b")),
        )),
        program.send("C", program.name("y")),
    ))

    with pytest.raises(SemanticValidationError, match="Parallel combinational branches conflict"):
        _analyze(program, body)


@pytest.mark.parametrize("branches", (
    lambda program: (
        program.assign("y", program.name("a")),
        program.assign("z", program.name("y")),
    ),
    lambda program: (
        program.assign("z", program.name("y")),
        program.assign("y", program.name("a")),
    ),
), ids=("write_read", "read_write"))
def test_parallel_whole_variable_read_write_conflicts_are_rejected(branches) -> None:
    program = _Program()
    body = Sequence((
        program.receive("A", "a"),
        program.receive("Y", "y"),
        Parallel(branches(program)),
        program.send("C", program.name("z")),
    ))

    with pytest.raises(SemanticValidationError, match="Parallel combinational branches conflict"):
        _analyze(program, body)


def test_parallel_disjoint_writes_and_shared_reads_remain_legal() -> None:
    program = _Program()
    body = Sequence((
        program.receive("A", "a"),
        program.receive("B", "b"),
        Parallel((
            program.assign("y", program.name("a")),
            program.assign("z", program.name("a")),
        )),
        program.send("C", program.name("y")),
        program.send("D", program.name("z")),
    ))

    _analyze(program, body)


def test_sequential_reassignment_and_guarded_definitions_remain_legal() -> None:
    program = _Program()
    sequential = Sequence((
        program.receive("A", "a"),
        program.assign("y", program.name("a")),
        program.assign("y", Expression("unary", operator="~", operands=(program.name("y"),))),
        program.send("B", program.name("y")),
    ))
    guarded = Sequence((
        program.receive("A", "a"),
        If(program.condition("sel"), program.assign("y", program.name("a")),
           program.assign("y", Expression("literal", value="1'b0"))),
        program.send("B", program.name("y")),
    ))

    _analyze(program, sequential)
    _analyze(program, guarded)


@pytest.mark.parametrize("source", (
    '''module bit_receive(Channel #(1) A, B); logic [7:0] x; always begin
        A.Receive(x[0]); B.Send(x[0]);
    end endmodule''',
    '''module bit_assign(input logic value, Channel #(1) A, B); logic a, x; always begin
        A.Receive(a); x[0] = value; B.Send(x[0]);
    end endmodule''',
    '''module range_receive(Channel #(4) A, B); logic [7:0] x; always begin
        A.Receive(x[3:0]); B.Send(x[3:0]);
    end endmodule''',
    '''module range_assign(input logic [3:0] value, Channel #(1) A, Channel #(4) B); logic a; logic [7:0] x; always begin
        A.Receive(a); x[3:0] = value; B.Send(x[3:0]);
    end endmodule''',
), ids=("bit_receive", "bit_assign", "range_receive", "range_assign"))
def test_static_selected_lvalue_targets_are_supported(source: str) -> None:
    _analyze_source(source)


def test_disjoint_static_ranges_jointly_cover_a_whole_variable_read() -> None:
    _analyze_source('''module ranges(Channel #(4) A, B, Channel #(8) C); logic [7:0] x;
always begin A.Receive(x[3:0]); B.Receive(x[7:4]); C.Send(x); end
endmodule''')


def test_selected_read_requires_full_coverage_but_distinct_bits_remain_independent() -> None:
    with pytest.raises(SemanticValidationError, match="conditional receive data x is not valid"):
        _analyze_source('''module partial(Channel #(1) A, B, Channel #(8) C); logic [7:0] x;
always begin A.Receive(x[0]); B.Receive(x[1]); C.Send(x); end
endmodule''')
    _analyze_source('''module selected(Channel #(1) A, B, C); logic [7:0] x;
always begin A.Receive(x[0]); B.Receive(x[1]); C.Send(x[1]); end
endmodule''')


def test_overlapping_sequential_write_replaces_only_its_interval() -> None:
    program = _Program()
    value = program.condition("value")
    receive = program.receive("A", "x")
    assign = Assign(_select_bit(program.name("x")), value)
    send = program.send("B", _select_bit(program.name("x")))
    validated = _analyze(program, Sequence((receive, assign, send)))

    data_sources = {
        dependency.source
        for dependency in validated.dependencies
        if dependency.target.operation is send
        and dependency.kind is SemanticDependencyKind.DATA
    }
    assert data_sources == {next(source for source in validated.decomposed.transaction.combinational
                                  if source.operation is assign)}


def test_guarded_branch_merges_preserve_selected_bit_coverage() -> None:
    program = _Program()
    select = program.condition("select")
    body = Sequence((
        program.receive("A", "a"),
        If(select,
           Assign(_select_bit(program.name("x")), program.name("a")),
           Assign(_select_bit(program.name("x")), program.name("a"))),
        program.send("B", _select_bit(program.name("x"))),
    ))
    _analyze(program, body)


def test_parallel_static_slices_are_interval_aware() -> None:
    _analyze_source('''module disjoint(Channel #(1) A, B, C, D); logic a, b; logic [7:0] x;
always begin
  A.Receive(a); B.Receive(b);
  fork x[0] = a; x[1] = b; join
  C.Send(x[0]); D.Send(x[1]);
end
endmodule''')

    with pytest.raises(SemanticValidationError, match="Parallel combinational branches conflict"):
        _analyze_source('''module overlap(Channel #(2) A, Channel #(1) B, C); logic [1:0] a; logic b; logic [7:0] x;
always begin
  A.Receive(a); B.Receive(b);
  fork x[1:0] = a; x[0] = b; join
  C.Send(x[0]);
end
endmodule''')


@pytest.mark.parametrize(("source", "match"), (
    ('''module dynamic(input logic i, Channel #(1) A, B); logic [7:0] x; always begin
        A.Receive(x[i]); B.Send(x[0]); end endmodule''', 'literal integer'),
    ('''module parameter_index #(parameter int P = 0) (Channel #(1) A, B); logic [7:0] x; always begin
        A.Receive(x[P]); B.Send(x[0]); end endmodule''', 'literal integer'),
    ('''module symbolic #(parameter int W = 8) (Channel #(1) A, B); logic [W-1:0] x; always begin
        A.Receive(x[0]); B.Send(x[0]); end endmodule''', 'symbolic-width'),
    ('''module bounds(Channel #(1) A, B); logic [7:0] x; always begin
        A.Receive(x[8]); B.Send(x[0]); end endmodule''', 'out of bounds'),
    ('''module range_bounds(Channel #(2) A, B); logic [7:0] x; always begin
        A.Receive(x[9:8]); B.Send(x[1:0]); end endmodule''', 'out of bounds'),
), ids=("dynamic", "parameter", "symbolic", "bit_bounds", "range_bounds"))
def test_unsupported_selected_lvalue_forms_fail_closed(source: str, match: str) -> None:
    with pytest.raises(SemanticValidationError, match=match):
        _analyze_source(source)


def test_selected_rvalue_send_expression_remains_legal() -> None:
    _analyze_source('''module m(Channel #(1) A, B); logic x; always begin
        A.Receive(x); B.Send(x[0]);
    end endmodule''')
