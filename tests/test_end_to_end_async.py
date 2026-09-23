"""End-to-end target-flow file-compilation tests."""
from pathlib import Path
import shutil
import subprocess

import pytest

from svcsp_compiler import compile_async_file
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

@pytest.mark.parametrize(('source_text', 'expected'), (
    ('''module parallel(interface A, B, C); logic x, y; always begin fork
A.Receive(x); B.Receive(y); join C.Send(x); end endmodule''', '.N(2)'),
    ('''module fanout(interface A, B, C); logic x; always begin
A.Receive(x); B.Send(x); C.Send(x); end endmodule''', '.M(2)'),
    ('''module conditional(interface A, B); logic c, x; always begin
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
