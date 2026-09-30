from pathlib import Path

import pytest
from pyslang.ast import (
    Compilation,
    ExpressionStatement,
    ParameterSymbol,
    ProceduralBlockSymbol,
    VariableSymbol,
)
from pyslang.syntax import SyntaxTree

from svcsp_compiler.semantic_frontend import (
    SemanticFrontendError,
    SemanticContext,
    parse_file,
    parse_text,
)


SOURCE = """\
module m #(parameter int W = 8) (input logic [7:0] in);
  logic [7:0] x;
  always begin
    x = in;
    x = x;
  end
endmodule
"""


def _procedure(module):
    visited = []
    module.visit(visited.append)
    return next(item for item in visited if isinstance(item, ProceduralBlockSymbol))


def test_text_frontend_retains_one_native_resolved_context() -> None:
    context = parse_text(SOURCE, "semantic_frontend.sv")

    assert isinstance(context, SemanticContext)
    assert isinstance(context.syntax_tree, SyntaxTree)
    assert isinstance(context.compilation, Compilation)
    assert context.root is context.compilation.getRoot()
    assert context.source_manager is context.syntax_tree.sourceManager

    assert len(context.root.topInstances) == 1
    module = context.root.topInstances[0].body
    assert module.name == "m"

    parameter = module.parameters[0]
    assert isinstance(parameter, ParameterSymbol)
    assert parameter.name == "W"
    assert parameter.value == 8

    x = module.find("x")
    assert isinstance(x, VariableSymbol)
    assert module.find("x") is x
    assert x.type.bitWidth == 8

    procedure = _procedure(module)
    statements = procedure.body.body.list
    assert len(statements) == 2
    assert all(isinstance(statement, ExpressionStatement) for statement in statements)

    first_assignment = statements[0].expr
    second_assignment = statements[1].expr
    assert first_assignment.left.symbol is x
    assert first_assignment.left.getSymbolReference() is x
    assert second_assignment.left.getSymbolReference() is x
    assert second_assignment.right.symbol is x
    assert second_assignment.right.getSymbolReference() is x

    assert x.syntax is not None
    assert statements[0].syntax is not None
    assert first_assignment.syntax is not None

    location = context.source_manager.getFullyExpandedLoc(x.location)
    assert str(context.source_manager.getFileName(location)) == "semantic_frontend.sv"
    assert context.source_manager.getLineNumber(location) == 2
    assert context.source_manager.getColumnNumber(location) == 15


@pytest.mark.parametrize(
    "source",
    (
        "module broken; logic x; always x = ; endmodule",
        "module broken; logic x; always x = missing; endmodule",
    ),
)
def test_invalid_diagnostics_raise_semantic_frontend_error(source: str) -> None:
    with pytest.raises(SemanticFrontendError):
        parse_text(source, "invalid.sv")


def test_parse_file_returns_native_resolved_context(tmp_path) -> None:
    source = tmp_path / "simple.sv"
    source.write_text(SOURCE)

    context = parse_file(source)

    assert isinstance(context.syntax_tree, SyntaxTree)
    assert isinstance(context.compilation, Compilation)
    assert context.root is context.compilation.getRoot()
    assert context.root.topInstances[0].body.find("x").type.bitWidth == 8


def test_parse_file_preprocesses_include_dirs_and_macros(tmp_path) -> None:
    include_dir = tmp_path / "include"
    include_dir.mkdir()
    (include_dir / "definitions.svh").write_text("""\
`define WIDTH 8
`define COPY_INPUT x = in;
""")
    source = tmp_path / "preprocessed.sv"
    source.write_text("""\
`include "definitions.svh"
module preprocessed #(parameter int W = `WIDTH) (input logic [`WIDTH-1:0] in);
  logic [`WIDTH-1:0] x;
  always begin
    `COPY_INPUT
    x = x;
  end
endmodule
""")

    context = parse_file(source, include_dirs=(include_dir,))
    module = context.root.topInstances[0].body
    x = module.find("x")
    assert isinstance(x, VariableSymbol)
    assert x.type.bitWidth == 8
    assert module.find("x") is x

    statements = _procedure(module).body.body.list
    assert len(statements) == 2
    first_assignment, second_assignment = (statement.expr for statement in statements)
    assert first_assignment.left.symbol is x
    assert second_assignment.right.symbol is x

    location = context.source_manager.getFullyExpandedLoc(first_assignment.sourceRange.start)
    assert Path(str(context.source_manager.getFileName(location))).resolve() == source.resolve()
    assert context.source_manager.getLineNumber(location) == 5


def test_parse_file_missing_source_raises_file_not_found(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_file(tmp_path / "missing.sv")


def test_parse_file_preprocessing_diagnostic_raises_semantic_frontend_error(tmp_path) -> None:
    source = tmp_path / "missing_include.sv"
    source.write_text('`include "missing.svh"\nmodule m; endmodule\n')

    with pytest.raises(SemanticFrontendError):
        parse_file(source)
