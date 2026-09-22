from dataclasses import fields, is_dataclass

from svcsp_compiler import (
    Assign, Expression, If, Parallel, Receive, Send, Sequence, Skip, lower_behavioral, parse_text,
)


def lower(source, filename='behavior.sv'):
    return lower_behavioral(parse_text(source, filename))


def test_standalone_receive_lowers_to_receive():
    ir = lower('module m(interface C); logic x; always C.Receive(x); endmodule')
    assert isinstance(ir.body, Receive)
    assert ir.body.channel.name == 'C'
    assert ir.body.target.name == 'x'


def test_standalone_send_lowers_to_send():
    ir = lower('module m(interface C); logic x; always C.Send(x); endmodule')
    assert isinstance(ir.body, Send)
    assert ir.body.channel.name == 'C'
    assert ir.body.value.value == 'x'


def test_standalone_assignment_lowers_to_assign():
    ir = lower('module m; logic x; always x = x; endmodule')
    assert isinstance(ir.body, Assign)
    assert ir.body.target.name == 'x'
    assert ir.body.value.value == 'x'


def test_sequential_communications_remain_in_source_order():
    ir = lower('''module m(interface A, B); logic x; always begin
A.Receive(x); B.Send(x); end endmodule''')
    assert isinstance(ir.body, Sequence)
    assert [type(item) for item in ir.body.items] == [Receive, Send]
    assert [item.channel.name for item in ir.body.items] == ['A', 'B']


def test_sequential_receive_assign_send():
    ir = lower('''module m(interface L, R); logic data, increment; always begin
L.Receive(data); data = data + increment; R.Send(data); end endmodule''')
    assert isinstance(ir.body, Sequence)
    receive, assign, send = ir.body.items
    assert isinstance(receive, Receive) and receive.channel.name == 'L' and receive.target.name == 'data'
    assert isinstance(assign, Assign) and assign.target.name == 'data'
    assert assign.value.form == 'binary' and assign.value.operator == '+'
    assert isinstance(send, Send) and send.channel.name == 'R' and send.value.value == 'data'


def test_fork_join_lowers_to_parallel():
    ir = lower('''module m(interface A, B); logic a, b; always fork
A.Receive(a); B.Receive(b); join endmodule''')
    assert isinstance(ir.body, Parallel)
    assert [(branch.channel.name, branch.target.name) for branch in ir.body.branches] == [('A', 'a'), ('B', 'b')]


def test_if_else_lowers_to_if():
    ir = lower('''module m(interface A, B); logic x; always
if (x) A.Send(x); else B.Send(x); endmodule''')
    assert isinstance(ir.body, If)
    assert ir.body.condition.form == 'name' and ir.body.condition.value == 'x'
    assert isinstance(ir.body.then_branch, Send)
    assert isinstance(ir.body.else_branch, Send)


def test_conditional_send_remains_inside_if():
    ir = lower('''module m(interface L, R); logic data; always begin
L.Receive(data); if (data) R.Send(data); else data = data; end endmodule''')
    assert isinstance(ir.body, Sequence)
    assert isinstance(ir.body.items[1], If)
    assert isinstance(ir.body.items[1].then_branch, Send)
    assert isinstance(ir.body.items[1].else_branch, Assign)


def test_conditional_receive_remains_inside_if():
    ir = lower('''module m(interface C, L); logic enable, data; always begin
C.Receive(enable); if (enable) L.Receive(data); else data = enable; end endmodule''')
    conditional = ir.body.items[1]
    assert isinstance(conditional, If)
    assert isinstance(conditional.then_branch, Receive)
    assert isinstance(conditional.else_branch, Assign)


def test_nested_sequence_parallel_and_if_are_preserved():
    ir = lower('''module m(interface A, B, C); logic a, b; always begin
begin fork A.Receive(a); begin if (a) B.Send(a); else C.Receive(b); end join end
end endmodule''')
    assert isinstance(ir.body, Sequence)
    nested_sequence = ir.body.items[0]
    assert isinstance(nested_sequence, Sequence)
    assert isinstance(nested_sequence.items[0], Parallel)
    assert isinstance(nested_sequence.items[0].branches[1], Sequence)
    assert isinstance(nested_sequence.items[0].branches[1].items[0], If)


def test_source_locations_are_preserved():
    ir = lower('''module m(interface C); logic x; always begin
  C.Receive(x);
end endmodule''', 'locations.sv')
    receive = ir.body.items[0]
    assert receive.location.file == 'locations.sv'
    assert (receive.location.line, receive.location.column) == (2, 3)


def test_ir_contains_no_pyslang_objects():
    ir = lower('module m(interface C); logic x; always C.Send(x); endmodule')

    def visit(value):
        assert not type(value).__module__.startswith('pyslang')
        if is_dataclass(value):
            for field in fields(value):
                visit(getattr(value, field.name))
        elif isinstance(value, tuple):
            for item in value:
                visit(item)

    visit(ir)


def test_selected_channel_endpoints_remain_distinguishable():
    ir = lower('''module m(interface A[2]); logic x; always begin
A[0].Receive(x); A[0].Send(x); A[1].Send(x); end endmodule''')
    receive, same_endpoint_send, send = ir.body.items
    assert receive.channel.name == same_endpoint_send.channel.name == send.channel.name == 'A'
    assert receive.channel.selectors[0].operands[0].value == '0'
    assert send.channel.selectors[0].operands[0].value == '1'
    assert receive.channel == same_endpoint_send.channel
    assert receive.channel != send.channel


def test_selected_receive_and_assignment_targets_preserve_variable_identity():
    ir = lower('''module m(interface A); logic [1:0] x; always begin
A.Receive(x[0]); x[1] = x[0]; end endmodule''')
    receive, assign = ir.body.items
    assert isinstance(receive.target, Expression) and receive.target.form == 'select'
    assert isinstance(assign.target, Expression) and assign.target.form == 'select'
    assert receive.target.variable == assign.target.variable
    assert receive.target.operands[0].operands[0].value == '0'
    assert assign.target.operands[0].operands[0].value == '1'


def test_block_local_shadowing_has_distinct_variable_identities():
    ir = lower('''module m(interface A, B); logic x; always begin
begin logic x; A.Receive(x); end B.Send(x); end endmodule''')
    receive = ir.body.items[0].items[0]
    send = ir.body.items[1]
    assert receive.target.name == send.value.value == 'x'
    assert receive.target != send.value.variable
    assert receive.target.location != send.value.variable.location


def test_nested_scopes_with_same_variable_name_remain_distinct():
    ir = lower('''module m(interface A, B, C); logic x; always begin
begin logic x; begin logic x; A.Receive(x); end B.Send(x); end C.Send(x); end endmodule''')
    outer, middle, inner = ir.variables
    receive = ir.body.items[0].items[0].items[0]
    middle_send = ir.body.items[0].items[1]
    outer_send = ir.body.items[1]
    assert receive.target == inner
    assert middle_send.value.variable == middle
    assert outer_send.value.variable == outer
    assert len({outer, middle, inner}) == 3


def test_empty_begin_end_is_an_empty_sequence():
    ir = lower('module m; always begin end endmodule')
    assert isinstance(ir.body, Sequence) and ir.body.items == ()


def test_empty_fork_join_is_an_empty_parallel():
    ir = lower('module m; always fork join endmodule')
    assert isinstance(ir.body, Parallel) and ir.body.branches == ()


def test_absent_else_lowers_to_skip():
    ir = lower('module m; logic x; always if (x) x = x; endmodule')
    assert isinstance(ir.body, If) and isinstance(ir.body.else_branch, Skip)


def test_nested_empty_branches_are_preserved():
    ir = lower('''module m; logic x; always begin
if (x) begin fork join end else begin if (x) ; end
end endmodule''')
    assert isinstance(ir.body, Sequence)
    conditional = ir.body.items[0]
    assert isinstance(conditional.then_branch, Sequence)
    assert isinstance(conditional.then_branch.items[0], Parallel)
    assert isinstance(conditional.else_branch, Sequence)
    assert isinstance(conditional.else_branch.items[0], If)
    assert isinstance(conditional.else_branch.items[0].then_branch, Skip)
