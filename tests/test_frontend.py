import json
from pathlib import Path

import pytest

from svcsp_compiler import FrontendError, parse_file, parse_text
from svcsp_compiler.frontend import walk

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


def directions(result):
    return {c['name']: c['direction'] for c in result['channels']}


@pytest.mark.parametrize('name, expected', [
    ('simple_buffer', {'L': 'input', 'R': 'output'}),
    ('fork_join_adder', {'A': 'input', 'B': 'input', 'R': 'output'}),
    ('conditional_send', {'L': 'input', 'R': 'output'}),
    ('conditional_receive', {'Control': 'input', 'L': 'input', 'R': 'output'}),
])
def test_examples(name, expected):
    result = parse_file(EXAMPLES / f'{name}.sv')
    assert result['module'] == name
    assert directions(result) == expected
    assert result['variables']
    json.dumps(result)


def test_fork_and_assignment_structure():
    result = parse_file(EXAMPLES / 'fork_join_adder.sv')
    items = result['always']['statement']['items']
    assert items[0]['kind'] == 'ParallelBlockStatement'
    assert items[0]['end'] == 'join'
    assert len(items[0]['items']) == 2
    assignment = items[1]['expr']
    assert assignment['kind'] == 'AssignmentExpression'
    assert assignment['left']['identifier'] == 'sum'
    assert assignment['right']['kind'] == 'AddExpression'
    assert [op['channel'] for op in result['operations']] == ['A', 'B', 'R']


def test_nested_if_else_and_fork_paths():
    result = parse_text('''module m(interface L, R);
logic x;
always begin
  if (x) fork
    L.Receive(x);
    begin if (x) R.Send(x); else R.Send(0); end
  join else x = 0;
end endmodule''', 'nested.sv')
    conditional = result['always']['statement']['items'][0]
    assert conditional['statement']['kind'] == 'ParallelBlockStatement'
    assert conditional['elseClause']['clause']['expr']['kind'] == 'AssignmentExpression'
    assert len([n for n in walk(conditional) if n['kind'] == 'ConditionalStatement']) == 2
    assert result['operations'][0]['location'] == {'file': 'nested.sv', 'line': 5, 'column': 5}
    assert 'elseClause' in result['operations'][2]['syntax_path']


def test_types_dimensions_initializers_and_precedence():
    result = parse_text("module m; logic signed [7:0] a=1,b; reg r; bit bits[2]; always b=(a+2)*3; endmodule")
    assert [v['name'] for v in result['variables']] == ['a', 'b', 'r', 'bits']
    assert result['variables'][0]['type']['dimensions']
    assert result['variables'][0]['initializer']['expr']['literal'] == '1'
    assert result['variables'][3]['dimensions']
    expr = result['always']['statement']['expr']['right']
    assert expr['kind'] == 'MultiplyExpression'
    assert expr['left']['expression']['kind'] == 'AddExpression'


def test_channels_inheritance_modports_arrays_and_mixed_direction():
    result = parse_text('''module m(Channel A, B, Channel.rx C[2], interface unused);
logic x; always begin A.Receive(x); B.Send(x); C[0].Receive(x); C[1].Send(x); end endmodule''')
    assert directions(result) == {'A': 'input', 'B': 'output', 'C': 'bidirectional', 'unused': 'unknown'}
    assert result['channels'][2]['modport'] == 'rx'
    assert result['channels'][2]['dimensions']


def test_explicit_scalar_and_packed_data_inputs_are_preserved_separately_from_channels():
    result = parse_text('''module m(input logic sel, input logic [7:0] control, interface A);
logic x; always if (sel) A.Send(control); endmodule''')

    assert directions(result) == {'A': 'output'}
    assert [item['name'] for item in result['external_inputs']] == ['sel', 'control']
    assert [item['payload_type']['width']['bits'] for item in result['external_inputs']] == [1, 8]
    assert [item['name'] for item in result['variables']] == ['x']


@pytest.mark.parametrize('declaration', (
    'output logic result',
    'inout logic control',
))
def test_non_input_data_ports_are_rejected(declaration):
    with pytest.raises(
        FrontendError,
        match='only input data ports are supported',
    ):
        parse_text(f'module m({declaration}); always begin end endmodule')


@pytest.mark.parametrize('source', (
    '''module m(input logic sel);
always sel = 1'b0;
endmodule''',
    '''module m(input logic sel, interface A);
always A.Receive(sel);
endmodule''',
))
def test_external_inputs_cannot_be_assignment_or_receive_targets(source):
    with pytest.raises(
        FrontendError,
        match='expected a local variable receive/assignment target',
    ):
        parse_text(source)

def test_custom_type_and_escaped_identifiers():
    result = parse_text(r'module \m.name (Link \in.port ); logic x; always \in.port .Receive(x); endmodule', channel_types=('Link',))
    assert result['module'] == 'm.name'
    assert directions(result) == {'in.port': 'input'}


def test_block_variables_and_scope():
    result = parse_text('module m(interface C); logic x; always begin begin bit x; x=1; end C.Receive(x); end endmodule')
    assert len(result['variables']) == 2
    assert result['variables'][0]['scope'] != result['variables'][1]['scope']
    assert directions(result) == {'C': 'input'}


def test_source_locations_are_preserved_for_representative_semantics():
    result = parse_text('''module m #(parameter int W = 8) (Channel #(W) C);
logic [W-1:0] x;
always begin
  C.Send(x);
end
endmodule
''', 'locations.sv')
    assert result['location'] == {'file': 'locations.sv', 'line': 1, 'column': 1}
    assert result['parameters'][0]['location'] == {'file': 'locations.sv', 'line': 1, 'column': 26}
    assert result['channels'][0]['location'] == {'file': 'locations.sv', 'line': 1, 'column': 47}
    assert result['variables'][0]['location'] == {'file': 'locations.sv', 'line': 2, 'column': 15}
    assert result['always']['location'] == {'file': 'locations.sv', 'line': 3, 'column': 1}
    assert result['always']['statement']['location'] == {'file': 'locations.sv', 'line': 3, 'column': 8}
    assert result['operations'][0]['location'] == {'file': 'locations.sv', 'line': 4, 'column': 3}


def test_parameter_int_declarations_preserve_source_order_defaults_and_locations():
    result = parse_text('''module m #(parameter int W = 8, parameter int V) (Channel #(W) C);
logic [V-1:0] x;
always C.Send(x);
endmodule
''', 'parameters.sv')

    assert [parameter['name'] for parameter in result['parameters']] == ['W', 'V']
    assert [parameter['default'] for parameter in result['parameters']] == ['8', None]
    assert [parameter['module'] for parameter in result['parameters']] == ['m', 'm']
    assert all(parameter['location']['file'] == 'parameters.sv' for parameter in result['parameters'])


def test_supported_selected_unary_binary_conditional_and_concatenation_expression():
    result = parse_text('''module m(interface C); logic [7:0] a, b; logic select; always
C.Send(select ? ~a[0] : {a[3:0], b[3:0]} + b[7:0]); endmodule''')
    expression = result['operations'][0]['argument']
    assert expression['kind'] == 'ConditionalExpression'
    assert expression['left']['kind'] == 'UnaryBitwiseNotExpression'
    assert expression['left']['operand']['kind'] == 'IdentifierSelectName'
    assert expression['right']['kind'] == 'AddExpression'
    assert expression['right']['left']['kind'] == 'ConcatenationExpression'
    assert [item['kind'] for item in expression['right']['left']['expressions']] == [
        'IdentifierSelectName', 'IdentifierSelectName'
    ]
    assert expression['right']['right']['kind'] == 'IdentifierSelectName'


@pytest.mark.parametrize('source, message', [
    ('module m; endmodule', 'exactly one top-level always'),
    ('module m; always begin end always begin end endmodule', 'exactly one top-level always'),
    ('module m; always begin end endmodule module n; always begin end endmodule', 'exactly one module'),
    ('module m; initial ; endmodule', 'module member'),
    ('module m; logic x; always x <= 1; endmodule', 'blocking assignment'),
    ('module m; always fork ; join_any endmodule', 'only fork/join'),
    ('module m; always #1; endmodule', 'unsupported frontend statement'),
    ('module m; always forever ; endmodule', 'unsupported frontend statement'),
    ('module m(interface C); logic x; always C.send(x); endmodule', 'declared channel'),
    ('module m(interface C); logic x; always C.Send(); endmodule', 'one positional'),
    ('module m(interface C); logic x; always C.Receive(1); endmodule', 'target'),
    ('module m(interface C); always C.Send(x); endmodule', 'declared local variable'),
    ('module m(interface C); logic x; always begin bit C; C.Send(x); end endmodule', 'declared channel'),
    ('module m; logic x,x; always begin end endmodule', 'duplicate declaration'),
    ('module m; int x; always begin end endmodule', 'logic/reg/bit'),
    ('module m; logic x; always x = helper(); endmodule', 'unsupported data expression'),
])
def test_unsupported_or_invalid_source_fails_closed(source, message):
    with pytest.raises(FrontendError, match=message):
        parse_text(source)


def test_parser_error_location_and_missing_file(tmp_path):
    with pytest.raises(FrontendError, match=r'broken.sv:3'):
        parse_text('module m(interface C);\nlogic x;\nalways C.Receive(x)\nendmodule', 'broken.sv')
    with pytest.raises(FileNotFoundError):
        parse_file(tmp_path / 'missing.sv')


def test_preprocessing_and_comment_exclusion(tmp_path):
    (tmp_path / 'defs.svh').write_text('`define RECV(c,v) c.Receive(v)\n')
    source = tmp_path / 'm.sv'
    source.write_text('''`include "defs.svh"
module m(interface C, R); logic x;
// R.Receive(x);
always begin
`RECV(C,x);
`ifdef UNUSED
R.Receive(x);
`else
R.Send(x);
`endif
end endmodule''')
    assert directions(parse_file(source, include_dirs=[tmp_path])) == {'C': 'input', 'R': 'output'}
    with pytest.raises(FrontendError):
        parse_text('`include "absent.svh"\nmodule m; always begin end endmodule')


@pytest.mark.parametrize('statement', [
    'x = #1 0;', 'x = (x += 1);', 'if (x matches 1) x=0;',
])
def test_no_hidden_timing_side_effects_or_patterns(statement):
    with pytest.raises(FrontendError):
        parse_text(f'module m; logic x; always begin {statement} end endmodule')


@pytest.mark.parametrize('argument', ['$random', 'this', '$root'])
def test_special_expression_names_fail_closed(argument):
    with pytest.raises(FrontendError, match='unsupported data expression'):
        parse_text(f'module m(interface C); logic x; always C.Send({argument}); endmodule')


@pytest.mark.parametrize('dimension', ['helper():0', 'x++:0'])
def test_declaration_dimensions_use_expression_validation(dimension):
    with pytest.raises(FrontendError, match='unsupported data expression'):
        parse_text(f'module m; logic [{dimension}] x; always begin end endmodule')
