"""End-to-end target-flow file-compilation tests."""
from pathlib import Path
import shutil
import subprocess

import pytest

from svcsp_compiler import FrontendError, SemanticValidationError, compile_async_file
from svcsp_compiler.transaction import TransactionStructureError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests' / 'fixtures' / 'receive_add_send.sv'
RTL_LIBRARY = tuple(sorted((ROOT / 'rtl_lib').glob('**/*.sv')))
IVERILOG = shutil.which('iverilog')


def test_target_fixture_compiles_through_the_complete_async_flow(tmp_path: Path) -> None:
    rtl = compile_async_file(FIXTURE)
    generated = tmp_path / "target_receive_add_send.sv"
    generated.write_text(rtl)

    # receive_add_send is a 1R1S transaction.
    # Per the documented M6 architecture, both sides use direct connections
    # around exactly one ordinary BODY half-buffer controller.
    assert "four_phase_half_buffer_controller" in rtl
    assert "bundled_data_latch_bank" in rtl
    assert "bundled_data_matched_delay" in rtl
    assert ".WIDTH(8)" in rtl

    # N=1: no request join or input-ACK fanout.
    assert "four_phase_request_join" not in rtl
    assert "four_phase_ack_fanout" not in rtl

    # M=1: no request fanout or output-ACK join.
    assert "four_phase_request_fanout" not in rtl
    assert "four_phase_ack_join" not in rtl

    # Retired generalized topology must not reappear.
    assert "four_phase_input_join" not in rtl
    assert "four_phase_output_fork" not in rtl
    assert "four_phase_output_completion" not in rtl
    assert "bundled_data_storage" not in rtl

    if not IVERILOG:
        pytest.skip("Icarus Verilog is not available")

    compile_result = subprocess.run(
        [
            IVERILOG,
            "-g2012",
            "-s",
            "receive_add_send",
            "-o",
            str(tmp_path / "generated"),
            *(str(source) for source in RTL_LIBRARY),
            str(generated),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr


def test_target_entrypoint_emits_compile_valid_source_module_parameters(tmp_path: Path) -> None:
    source = tmp_path / 'parameterized.sv'
    source.write_text('''
module parameterized #(parameter int W = 8) (Channel #(W) A, B);
logic [W-1:0] x;
always begin A.Receive(x); B.Send(x); end
endmodule
''')
    rtl = compile_async_file(source)
    generated = tmp_path / 'parameterized_generated.sv'
    generated.write_text(rtl)

    assert rtl.startswith('module parameterized #(\n    parameter int W = 8\n) (\n')
    assert '.WIDTH(W)' in rtl

    if not IVERILOG:
        pytest.skip('Icarus Verilog is not available')
    result = subprocess.run(
        [
            IVERILOG,
            '-g2012',
            '-s',
            'parameterized',
            '-o',
            str(tmp_path / 'parameterized'),
            *(str(library) for library in RTL_LIBRARY),
            str(generated),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(('source_text', 'expected'), (
    ('''module parallel(interface A, B, C); logic x, y; always begin fork
A.Receive(x); B.Receive(y); join C.Send(x); end endmodule''', '.N(2)'),
    ('''module fanout(interface A, B, C); logic x; always begin
A.Receive(x); B.Send(x); C.Send(x); end endmodule''', '.M(2)'),
    ('''module conditional(input logic c, interface A, B); logic x; always begin
A.Receive(x); if (c) B.Send(x); end endmodule''', 'en_send_controller'),
))
def test_target_entrypoint_accepts_supported_transaction_topologies(
    tmp_path: Path, source_text: str, expected: str,
) -> None:
    source = tmp_path / 'supported.sv'
    source.write_text(source_text)
    assert expected in compile_async_file(source)


def test_target_entrypoint_rejects_receive_after_send(tmp_path: Path) -> None:
    source = tmp_path / 'unsupported.sv'
    source.write_text('''module unsupported(interface A, B); logic x, y; always begin
A.Receive(x); B.Send(x); A.Receive(y); end endmodule''')
    with pytest.raises(TransactionStructureError, match='Receive occurs after'):
        compile_async_file(source)


def test_target_entrypoint_rejects_invalid_conditional_receive_data_use(
    tmp_path: Path,
) -> None:
    source = tmp_path / 'invalid_conditional_receive.sv'
    source.write_text('''module invalid_conditional_receive(input logic select, interface A, B);
logic x;
always begin
if (select) A.Receive(x);
B.Send(x);
end
endmodule''')

    with pytest.raises(SemanticValidationError, match='conditional receive data'):
        compile_async_file(source)


@pytest.mark.parametrize(('source_text', 'match'), (
    ('''module partial_selected_read(Channel #(1) A, Channel #(8) B); logic [7:0] x; always begin
A.Receive(x[0]); B.Send(x); end endmodule''',
     'conditional receive data x is not valid'),
    ('''module dynamic_selected_target(Channel #(1) A, B); logic [7:0] x; logic [2:0] i; always begin
A.Receive(x[i]); B.Send(1'b0); end endmodule''',
     'literal integer'),
    ('''module parameter_selected_target #(parameter int P = 0) (Channel #(1) A, B); logic [7:0] x; always begin
A.Receive(x[P]); B.Send(1'b0); end endmodule''',
     'literal integer'),
    ('''module symbolic_selected_target #(parameter int W = 8) (Channel #(1) A, B); logic [W-1:0] x; always begin
A.Receive(x[0]); B.Send(1'b0); end endmodule''',
     'symbolic-width'),
    ('''module out_of_bounds_selected_target(Channel #(1) A, B); logic [7:0] x; always begin
A.Receive(x[8]); B.Send(1'b0); end endmodule''',
     'out of bounds'),
    ('''module parallel_selected_overlap(Channel #(4) A, Channel #(2) B, Channel #(1) C); logic [3:0] a; logic [1:0] b; logic [3:0] x; always begin
A.Receive(a); B.Receive(b); fork x[3:0] = a; x[2:1] = b; join C.Send(a[0]); end endmodule''',
     'Parallel combinational branches conflict'),
    ('''module concurrent_selected_receives(Channel #(4) A, Channel #(2) B, Channel #(4) C); logic [7:0] x; always begin
A.Receive(x[3:0]); B.Receive(x[2:1]); C.Send(x[3:0]); end endmodule''',
     'concurrent Receive targets overlap'),
))
def test_target_entrypoint_rejects_unsupported_or_incomplete_selected_lvalues(
    tmp_path: Path, source_text: str, match: str,
) -> None:
    source = tmp_path / 'r9b_invalid_selected_lvalue.sv'
    source.write_text(source_text)

    with pytest.raises(SemanticValidationError, match=match):
        compile_async_file(source)


@pytest.mark.parametrize(('source_text', 'error', 'match'), (
    ('''module same_receive(Channel #(1) A, B, C); logic x; always begin
A.Receive(x); B.Receive(x); C.Send(x); end endmodule''',
     SemanticValidationError, 'concurrent Receive targets overlap'),
    ('''module parallel_conflict(Channel #(1) A, B, C); logic a, b, y; always begin
A.Receive(a); B.Receive(b); fork y = a; y = b; join C.Send(y); end endmodule''',
     SemanticValidationError, 'Parallel combinational branches conflict'),
    ('''module external_receive(input logic sel, Channel #(1) A, B); always begin
A.Receive(sel); B.Send(1'b0); end endmodule''',
     FrontendError, 'local variable receive/assignment target'),
))
def test_target_entrypoint_rejects_invalid_write_and_parallel_cases(
    tmp_path: Path, source_text: str, error: type[Exception], match: str,
) -> None:
    source = tmp_path / 'r9a_invalid.sv'
    source.write_text(source_text)

    with pytest.raises(error, match=match):
        compile_async_file(source)
