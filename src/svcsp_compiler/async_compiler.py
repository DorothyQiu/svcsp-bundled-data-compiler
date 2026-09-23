"""Public target-flow entry point for bundled-data asynchronous RTL."""
from __future__ import annotations

from pathlib import Path

from .async_microarchitecture import lower_microarchitecture
from .async_rtl_codegen import emit_async_systemverilog
from .async_template_binding import bind_async_templates
from .behavioral_ir import lower_behavioral
from .communication_decomposition import decompose_transaction
from .frontend import parse_file
from .semantic_analysis import analyze_semantics
from .transaction import extract_transaction


def compile_async_file(path: str | Path, *, include_dirs: tuple[str | Path, ...] = ()) -> str:
    """Compile one supported source file through the authoritative M1--M7 flow."""

    frontend = parse_file(path, include_dirs=include_dirs)
    behavioral = lower_behavioral(frontend)
    transaction = extract_transaction(behavioral)
    decomposed = decompose_transaction(transaction)
    validated = analyze_semantics(decomposed)
    architecture = lower_microarchitecture(validated)
    bound = bind_async_templates(architecture)
    return emit_async_systemverilog(bound)
