from dataclasses import asdict

from svcsp_compiler import (
    Assign, BodyChannelDirection, BodyReceive, BodySend, CommunicationSite, DummyToken, If,
    NormalizedReceive, NormalizedSend, Receive, Send,
    Sequence, Skip, lower_behavioral, normalize_communication, parse_text,
)


def normalize(source):
    return normalize_communication(lower_behavioral(parse_text(source, 'normalize.sv')))


def condition_text(expression):
    if expression.form in {'name', 'literal'}:
        return expression.value
    if expression.form == 'unary':
        return expression.operator + condition_text(expression.operands[0])
    return '(' + condition_text(expression.operands[0]) + expression.operator + condition_text(expression.operands[1]) + ')'


def test_unconditional_receive_passes_through():
    ir = normalize('module m(interface A); logic x; always A.Receive(x); endmodule')
    assert isinstance(ir.body, Receive)
    assert ir.wrappers == () and ir.enables == ()


def test_unconditional_send_passes_through():
    ir = normalize('module m(interface A); logic x; always A.Send(x); endmodule')
    assert isinstance(ir.body, Send)
    assert ir.wrappers == () and ir.enables == ()


def test_conditional_receive_becomes_receive_wrapper_with_dummy_token():
    ir = normalize('''module m(interface A); logic c, x; always
if (c) A.Receive(x); else x = c; endmodule''')
    assert isinstance(ir.body, If) and type(ir.body.then_branch) is CommunicationSite
    wrapper = ir.wrappers[0]
    assert isinstance(wrapper, NormalizedReceive)
    assert wrapper.enable.condition.value == 'c'
    assert isinstance(wrapper.disabled_token, DummyToken)
    assert wrapper.disabled_token.data_is_valid is False
    assert wrapper.consumes_external_when_enabled is True
    assert wrapper.forwards_real_data_when_enabled is True
    assert wrapper.acknowledges_external_when_disabled is False
    assert wrapper.provides_body_token_when_disabled is True
    body_receive = ir.body_communications[0]
    assert isinstance(body_receive, BodyReceive)
    assert body_receive.site is ir.body.then_branch is wrapper.site
    assert body_receive.channel is wrapper.body_channel is ir.body_channels[0]
    assert body_receive.enable is wrapper.enable
    assert body_receive.data_valid_when is wrapper.enable
    assert body_receive.disabled_token is wrapper.disabled_token
    assert body_receive.channel.direction is BodyChannelDirection.INTO_BODY


def test_conditional_send_becomes_send_wrapper_that_consumes_body_token():
    ir = normalize('''module m(interface A); logic c, x; always
if (c) A.Send(x); else x = c; endmodule''')
    assert isinstance(ir.body, If) and type(ir.body.then_branch) is CommunicationSite
    wrapper = ir.wrappers[0]
    assert isinstance(wrapper, NormalizedSend)
    assert wrapper.enable.condition.value == 'c'
    assert wrapper.consumes_body_token_always is True
    assert wrapper.communicates_externally_when_enabled is True
    assert wrapper.communicates_externally_when_disabled is False
    body_send = ir.body_communications[0]
    assert isinstance(body_send, BodySend)
    assert body_send.site is ir.body.then_branch is wrapper.site
    assert body_send.channel is wrapper.body_channel is ir.body_channels[0]
    assert body_send.enable is wrapper.enable
    assert body_send.payload_valid_when is wrapper.enable
    assert body_send.value is wrapper.value
    assert body_send.channel.direction is BodyChannelDirection.FROM_BODY


def test_if_else_communications_get_distinct_enables():
    ir = normalize('''module m(interface A, B); logic c, x; always
if (c) A.Send(x); else B.Receive(x); endmodule''')
    assert len(ir.wrappers) == len(ir.enables) == 2
    then_wrapper, else_wrapper = ir.wrappers
    assert isinstance(then_wrapper, NormalizedSend)
    assert isinstance(else_wrapper, NormalizedReceive)
    assert then_wrapper.enable.name != else_wrapper.enable.name
    assert then_wrapper.enable.condition.value == 'c'
    assert else_wrapper.enable.condition.operator == '!'
    assert type(ir.body.then_branch) is CommunicationSite
    assert type(ir.body.else_branch) is CommunicationSite


def test_nested_conditionals_combine_enable_guards():
    ir = normalize('''module m(interface A); logic a, b, x; always
if (a) if (b) A.Send(x); endmodule''')
    wrapper = ir.wrappers[0]
    assert isinstance(wrapper, NormalizedSend)
    assert wrapper.enable.condition.operator == '&&'
    assert [operand.value for operand in wrapper.enable.condition.operands] == ['a', 'b']


def test_selected_channel_endpoint_identity_is_preserved():
    ir = normalize('''module m(interface A[2]); logic c, x; always
if (c) A[1].Receive(x); endmodule''')
    wrapper = ir.wrappers[0]
    assert wrapper.endpoint.name == 'A'
    assert wrapper.endpoint.selectors[0].operands[0].value == '1'
    assert wrapper.endpoint == lower_behavioral(parse_text(
        'module m(interface A[2]); logic x; always A[1].Receive(x); endmodule'
    )).body.channel


def test_multiple_conditional_operations_on_one_channel_have_unique_enables():
    ir = normalize('''module m(interface A); logic c, x; always begin
if (c) A.Send(x); if (c) A.Send(x); end endmodule''')
    assert len(ir.wrappers) == 2
    assert {wrapper.enable.occurrence for wrapper in ir.wrappers} == {0, 1}
    assert ir.wrappers[0].endpoint == ir.wrappers[1].endpoint
    assert [communication.site for communication in ir.body_communications] == list(ir.communication_sites)
    assert [communication.channel for communication in ir.body_communications] == list(ir.body_channels)


def test_wrapper_preserves_variable_and_channel_identity():
    behavioral = lower_behavioral(parse_text('''module m(interface A); logic c, x; always
if (c) A.Receive(x); endmodule'''))
    ir = normalize_communication(behavioral)
    wrapper = ir.wrappers[0]
    assert wrapper.endpoint == behavioral.body.then_branch.channel
    assert wrapper.target == behavioral.body.then_branch.target


def test_disabled_receive_and_send_semantics_are_explicit():
    receive = normalize('''module m(interface A); logic c, x; always
if (c) A.Receive(x); endmodule''').wrappers[0]
    send = normalize('''module m(interface A); logic c, x; always
if (c) A.Send(x); endmodule''').wrappers[0]
    assert receive.disabled_token.data_is_valid is False
    assert receive.acknowledges_external_when_disabled is False
    assert send.consumes_body_token_always is True
    assert send.communicates_externally_when_disabled is False


def test_else_if_guards_include_negated_outer_conditions():
    ir = normalize('''module m(interface A, B, C); logic outer, inner, x; always
if (outer) A.Send(x); else if (inner) B.Send(x); else C.Send(x); endmodule''')
    assert [condition_text(wrapper.enable.condition) for wrapper in ir.wrappers] == [
        'outer', '(!outer&&inner)', '(!outer&&!inner)',
    ]


def test_nested_else_guards_compose_with_the_outer_then_condition():
    ir = normalize('''module m(interface A, B, C); logic outer, inner, x; always
if (outer) if (inner) A.Send(x); else B.Send(x); else C.Send(x); endmodule''')
    assert [condition_text(wrapper.enable.condition) for wrapper in ir.wrappers] == [
        '(outer&&inner)', '(outer&&!inner)', '!outer',
    ]


def test_selected_endpoints_remain_distinct_after_normalization():
    ir = normalize('''module m(interface A[2]); logic c, x; always begin
if (c) A[0].Send(x); if (c) A[1].Receive(x); end endmodule''')
    first, second = ir.wrappers
    assert first.endpoint.name == second.endpoint.name == 'A'
    assert first.endpoint.selectors[0].operands[0].value == '0'
    assert second.endpoint.selectors[0].operands[0].value == '1'
    assert first.endpoint != second.endpoint


def test_wrapper_and_enable_locations_match_original_communication():
    ir = normalize('''module m(interface A); logic c, x; always begin
  if (c)
    A.Receive(x);
end endmodule''')
    wrapper = ir.wrappers[0]
    assert (wrapper.location.line, wrapper.location.column) == (3, 5)
    assert (wrapper.enable.location.line, wrapper.enable.location.column) == (3, 5)


def test_normalization_does_not_mutate_behavioral_ir():
    behavioral = lower_behavioral(parse_text('''module m(interface A); logic c, x; always
if (c) A.Send(x); endmodule'''))
    body = behavioral.body
    snapshot = asdict(behavioral)
    normalize_communication(behavioral)
    assert behavioral.body is body
    assert asdict(behavioral) == snapshot
    assert isinstance(behavioral.body.then_branch, Send)
