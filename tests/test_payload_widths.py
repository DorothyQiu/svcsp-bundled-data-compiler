"""Payload-width coverage for the authoritative M1--M7 flow."""
import pytest

from svcsp_compiler.async_microarchitecture import lower_microarchitecture
from svcsp_compiler.async_rtl_codegen import emit_async_systemverilog
from svcsp_compiler.async_template_binding import AsyncTemplateBindingError, bind_async_templates
from svcsp_compiler.behavioral_ir import BehavioralIRError, PayloadWidth, lower_behavioral
from svcsp_compiler.communication_decomposition import decompose_transaction
from svcsp_compiler.frontend import parse_text
from svcsp_compiler.semantic_analysis import analyze_semantics
from svcsp_compiler.transaction import extract_transaction


def _lower(source: str):
    return lower_behavioral(parse_text(source, 'widths.sv'))


def _target(source: str):
    behavioral = _lower(source)
    transaction = extract_transaction(behavioral)
    decomposed = decompose_transaction(transaction)
    validated = analyze_semantics(decomposed)
    architecture = lower_microarchitecture(validated)
    bound = bind_async_templates(architecture)
    return behavioral, transaction, decomposed, validated, architecture, bound


def test_frontend_preserves_scalar_concrete_and_symbolic_declared_widths() -> None:
    scalar = parse_text('module m; logic x; reg y; bit z; always begin end endmodule')['variables']
    concrete = parse_text('module m; logic [7:0] x; always begin end endmodule')['variables'][0]['payload_type']
    symbolic = parse_text('''module m #(parameter int W = 8); logic [W-1:0] x;
always begin end endmodule''')['variables'][0]['payload_type']
    assert [variable['payload_type']['width']['bits'] for variable in scalar] == [1, 1, 1]
    assert concrete['width']['bits'] == 8
    assert symbolic['width']['bits'] is None and symbolic['width']['symbolic'] == 'W'


def test_channel_width_parameter_survives_frontend_and_behavioral_lowering() -> None:
    frontend = parse_text('''module m #(parameter int W = 8) (Channel #(W) C); logic [W-1:0] x;
always C.Send(x); endmodule''')
    behavioral = lower_behavioral(frontend)
    assert frontend['channels'][0]['payload_type']['width']['symbolic'] == 'W'
    assert behavioral.body.channel.payload_type.width.symbolic == 'W'


def test_eight_bit_width_survives_m3_through_m7() -> None:
    behavioral, transaction, decomposed, validated, architecture, bound = _target('''
module m(Channel #(8) A, B); logic [7:0] x, y, increment; always begin
  A.Receive(x); y = x + increment; B.Send(y);
end endmodule''')
    assert behavioral.variables[0].payload_type.width.bits == 8
    assert transaction.receives[0].operation.target.payload_type.width.bits == 8
    assert decomposed.body_receives[0].source.operation.target.payload_type.width.bits == 8
    assert validated.decomposed is decomposed
    assert architecture.storage.slots[0].body_send.source.operation.channel.payload_type.width.bits == 8
    assert all(port.width.bits == 8 for port in bound.module_ports if port.role == 'payload')
    assert 'logic [7:0] body_var_0_x;' in emit_async_systemverilog(bound)


def test_conditional_receive_and_send_bind_eight_bit_m7_stages() -> None:
    _, _, _, _, architecture, bound = _target('''
module m(Channel #(8) A, B); logic c; logic [7:0] x, y; always begin
  if (c) A.Receive(x); B.Send(y);
end endmodule''')
    stage = architecture.en_receive_stages[0]
    instance = next(item for item in bound.instances if item.source is stage)
    assert next(item.value for item in bound.parameter_bindings
                if item.instance_id == instance.id and item.formal_name == 'WIDTH').bits == 8

    _, _, _, _, architecture, bound = _target('''
module m(Channel #(8) A, B); logic c; logic [7:0] x; always begin
  A.Receive(x); if (c) B.Send(x);
end endmodule''')
    stage = architecture.en_send_stages[0]
    instance = next(item for item in bound.instances if item.source is stage)
    assert next(item.value for item in bound.parameter_bindings
                if item.instance_id == instance.id and item.formal_name == 'WIDTH').bits == 8


def test_handshake_and_enable_signals_remain_one_bit() -> None:
    _, _, _, _, architecture, bound = _target('''
module m(Channel #(8) A, B); logic c; logic [7:0] x; always begin
  A.Receive(x); if (c) B.Send(x);
end endmodule''')
    assert all(channel.width.bits == 1 for channel in architecture.enable_channels)
    assert all(signal.width.bits == 1 for signal in bound.signals
               if signal.kind in {'request', 'acknowledge', 'completion', 'control'} and
               signal.id not in {'input_join_req', 'input_join_ack', 'output_fork_launch',
                                 'output_fork_complete', 'stage_output_complete'})


def test_selected_endpoints_and_shadowed_variables_preserve_identity_and_width() -> None:
    behavioral, _, _, _, _, bound = _target('''
module m(Channel #(4) A[2], B); logic c; logic [3:0] x; always begin
  B.Receive(x); if (c) A[0].Send(x); if (c) A[1].Send(x);
end endmodule''')
    first, second = behavioral.body.items[1].then_branch.channel, behavioral.body.items[2].then_branch.channel
    assert first != second and first.payload_type.width.bits == second.payload_type.width.bits == 4
    assert {port.endpoint for port in bound.module_ports if port.flow == 'send'} == {first, second}

    shadowed = _lower('''module m(interface A, B); logic [7:0] x; always begin
begin logic [3:0] x; A.Receive(x); end B.Send(x); end endmodule''')
    inner, outer = shadowed.body.items[0].items[0].target, shadowed.body.items[1].value.variable
    assert inner is not outer
    assert (inner.payload_type.width.bits, outer.payload_type.width.bits) == (4, 8)


def test_unknown_and_incompatible_payload_widths_fail_closed_without_conversion() -> None:
    behavioral = _lower('''module m(interface A, B); logic x; always begin
  A.Receive(x); B.Send(5);
end endmodule''')
    validated = analyze_semantics(decompose_transaction(extract_transaction(behavioral)))
    with pytest.raises(AsyncTemplateBindingError, match='Send has no payload width'):
        bind_async_templates(lower_microarchitecture(validated))
    with pytest.raises(BehavioralIRError, match='incompatible payload widths for Receive'):
        _lower('module m(Channel #(8) C); logic [15:0] x; always C.Receive(x); endmodule')
    with pytest.raises(BehavioralIRError, match='incompatible payload widths for Send'):
        _lower('module m(Channel #(16) C); logic [7:0] x; always C.Send(x); endmodule')
    with pytest.raises(BehavioralIRError, match='incompatible payload widths for Send'):
        _lower('''module m(Channel #(8) B); logic c; logic [15:0] x; always
if (c) B.Send(x); endmodule''')


def test_symbolic_width_identity_is_preserved_and_mismatch_is_rejected() -> None:
    _, _, _, _, _, bound = _target('''module m #(parameter int W = 8) (Channel #(W) A, B);
logic [W-1:0] x; always begin A.Receive(x); B.Send(x); end endmodule''')
    payload = next(port for port in bound.module_ports if port.role == 'payload')
    assert payload.width.symbolic == 'W'
    assert payload.width.parameters[0].name == 'W'
    with pytest.raises(BehavioralIRError, match='incompatible payload widths for Send'):
        _lower('''module m #(parameter int W = 8, parameter int V = 8) (Channel #(W) C);
logic [V-1:0] x; always C.Send(x); endmodule''')
    with pytest.raises(Exception, match='undeclared symbolic width parameter: W'):
        parse_text('module m; logic [W-1:0] x; always begin end endmodule')


def test_payload_width_object_requires_exactly_one_representation() -> None:
    with pytest.raises(ValueError, match='payload width requires exactly one'):
        PayloadWidth()
