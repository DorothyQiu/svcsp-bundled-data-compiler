"""Public compilation entry points."""

from .backend import emit_verilog
from .lowering import CompileError, lower
from .parser import parse_files


def compile_ast(ast, *, top=None, parameters=None, channel_widths=None):
    """Compile the supported CSP subset to structural Verilog (without models)."""
    return emit_verilog(lower(ast, top=top, parameters=parameters, channel_widths=channel_widths))


def compile_files(paths, *, include_dirs=(), top=None, parameters=None, channel_widths=None):
    return compile_ast(parse_files(paths, include_dirs), top=top,
                       parameters=parameters, channel_widths=channel_widths)


__all__ = ["CompileError", "compile_ast", "compile_files", "lower"]
