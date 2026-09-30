"""Native pyslang parsing and semantic-resolution boundary."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pyslang import DiagnosticEngine, SourceManager
from pyslang.ast import Compilation, RootSymbol
from pyslang.syntax import SyntaxTree


class SemanticFrontendError(ValueError):
    """Source text has pyslang syntax or semantic diagnostics."""


@dataclass(frozen=True)
class SemanticContext:
    """One parsed source tree and its native pyslang semantic compilation."""

    syntax_tree: SyntaxTree
    compilation: Compilation
    root: RootSymbol
    source_manager: SourceManager


def _raise_on_errors(source_manager: SourceManager, diagnostics: object) -> None:
    if any(diagnostic.isError() for diagnostic in diagnostics):
        raise SemanticFrontendError(DiagnosticEngine.reportAll(source_manager, diagnostics))


def _resolve(syntax_tree: SyntaxTree) -> SemanticContext:
    source_manager = syntax_tree.sourceManager
    _raise_on_errors(source_manager, syntax_tree.diagnostics)

    compilation = Compilation()
    compilation.addSyntaxTree(syntax_tree)
    root = compilation.getRoot()
    _raise_on_errors(source_manager, compilation.getAllDiagnostics())

    return SemanticContext(syntax_tree, compilation, root, source_manager)


def parse_text(source: str, filename: str = "source.sv") -> SemanticContext:
    """Parse and semantically resolve one SystemVerilog source string once."""

    return _resolve(SyntaxTree.fromText(source, filename))


def parse_file(path: str | Path, include_dirs: tuple[str | Path, ...] = ()) -> SemanticContext:
    """Parse and semantically resolve one source file with pyslang preprocessing."""

    source_path = Path(path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    source_manager = SourceManager()
    for directory in include_dirs:
        source_manager.addUserDirectories(str(Path(directory).resolve()))
    return _resolve(SyntaxTree.fromFiles([str(source_path)], source_manager))
