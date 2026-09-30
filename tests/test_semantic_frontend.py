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
