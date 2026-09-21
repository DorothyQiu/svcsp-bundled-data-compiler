import pytest

from svcsp_compiler import BehavioralIRError, expression_payload_type, lower_behavioral, parse_text


def lower(source):
    return lower_behavioral(parse_text(source, 'expression_widths.sv'))


def test_matching_symbolic_binary_operands_prove_send_width():
    ir = lower('''module m #(parameter int W = 8) (Channel #(W) C); logic [W-1:0] a, b; always
C.Send(a + b); endmodule''')
    assert expression_payload_type(ir.body.value).width.symbolic == 'W'
    assert ir.body.channel.payload_type.width.symbolic == 'W'


def test_different_symbolic_binary_operands_fail_closed_for_send():
    source = '''module m #(parameter int W = 8, parameter int V = 8) (Channel #(W) C); logic [W-1:0] a; logic [V-1:0] b; always
C.Send(a + b); endmodule'''
    with pytest.raises(BehavioralIRError, match='cannot prove payload width for Send'):
        lower(source)


def test_known_channel_does_not_rescue_unknown_send_expression():
    with pytest.raises(BehavioralIRError, match='cannot prove payload width for Send'):
        lower('module m(Channel #(8) C); always C.Send(5); endmodule')


def test_bit_and_concrete_part_selects_have_proven_widths():
    bit = lower('module m(interface C); logic [7:0] x; always C.Send(x[0]); endmodule').body.value
    part = lower('module m(interface C); logic [7:0] x; always C.Send(x[3:0]); endmodule').body.value
    assert expression_payload_type(bit).width.bits == 1
    assert expression_payload_type(part).width.bits == 4


def test_receive_bit_select_requires_one_bit_channel():
    accepted = lower('module m(Channel #(1) C); logic [7:0] x; always C.Receive(x[0]); endmodule')
    assert accepted.body.channel.payload_type.width.bits == 1
    with pytest.raises(BehavioralIRError, match='incompatible payload widths for Receive'):
        lower('module m(Channel #(8) C); logic [7:0] x; always C.Receive(x[0]); endmodule')


def test_send_bit_select_requires_one_bit_channel():
    accepted = lower('module m(Channel #(1) C); logic [7:0] x; always C.Send(x[0]); endmodule')
    assert accepted.body.channel.payload_type.width.bits == 1
    with pytest.raises(BehavioralIRError, match='incompatible payload widths for Send'):
        lower('module m(Channel #(8) C); logic [7:0] x; always C.Send(x[0]); endmodule')


def test_channel_array_selection_keeps_full_payload_width():
    ir = lower('module m(Channel #(8) A[2]); logic [7:0] x; always A[0].Send(x); endmodule')
    assert ir.body.channel.selectors[0].operands[0].value == '0'
    assert ir.body.channel.payload_type.width.bits == 8


def test_matching_ternary_branches_prove_width_and_mismatched_branches_fail():
    accepted = lower('''module m(Channel #(8) C); logic c; logic [7:0] a, b; always
C.Send(c ? a : b); endmodule''')
    assert expression_payload_type(accepted.body.value).width.bits == 8
    with pytest.raises(BehavioralIRError, match='cannot prove payload width for Send'):
        lower('''module m(Channel #(8) C); logic c; logic [7:0] a; logic [15:0] b; always
C.Send(c ? a : b); endmodule''')


def test_assignment_accepts_independently_proven_matching_rhs_width():
    ir = lower('''module m; logic [7:0] x, a, b; always
x = a + b; endmodule''')
    assert expression_payload_type(ir.body.value).width.bits == 8
    assert ir.body.target.payload_type.width.bits == 8


def test_assignment_rejects_mixed_width_binary_rhs():
    with pytest.raises(BehavioralIRError, match='cannot prove payload width for assignment'):
        lower('''module m; logic [7:0] x, a; logic [15:0] b; always
x = a + b; endmodule''')


def test_assignment_rejects_unknown_rhs_even_with_known_target_width():
    with pytest.raises(BehavioralIRError, match='cannot prove payload width for assignment'):
        lower('''module m; logic [7:0] x; always
x = 5; endmodule''')


@pytest.mark.parametrize('source', [
    '''module m; logic [7:0] x; logic [15:0] a; always x = a; endmodule''',
    '''module m; logic [7:0] x; logic [3:0] a; always x = a; endmodule''',
])
def test_assignment_rejects_wider_or_narrower_rhs(source):
    with pytest.raises(BehavioralIRError, match='incompatible payload widths for assignment'):
        lower(source)


def test_selected_assignment_target_uses_selected_width_for_compatibility():
    accepted = lower('''module m; logic [7:0] x, a; always
x[0] = a[0]; endmodule''')
    assert expression_payload_type(accepted.body.target).width.bits == 1

    with pytest.raises(BehavioralIRError, match='incompatible payload widths for assignment'):
        lower('''module m; logic [7:0] x, a; always
x[3:0] = a; endmodule''')
