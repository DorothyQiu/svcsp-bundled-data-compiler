"""Parse real SV, then remove concrete-syntax scaffolding to form a source AST.

This is deliberately pre-elaboration: no binding, width inference, evaluation,
or handshake lowering is claimed. Unrecognized SV constructs remain structured
nodes so a subsequent lowering pass can explicitly accept or reject them.
"""

import json
from pathlib import Path

from pyslang import DiagnosticEngine, SourceManager
from pyslang.syntax import SyntaxTree

from .ast import Node


class ParseError(ValueError):
    pass


# Grammar delimiters carry no meaning after their child structure is captured.
# Operators, direction/type keywords, range operators and join modes are retained.
DELIMITERS = {
    "OpenParenthesis", "CloseParenthesis", "OpenBracket", "CloseBracket",
    "OpenBrace", "CloseBrace", "Comma", "Semicolon", "EndOfFile",
    "BeginKeyword", "EndKeyword", "EndModuleKeyword", "ModuleKeyword",
}
TRANSPARENT = {
    "ParenthesizedExpression", "SimplePropertyExpr", "SimpleSequenceExpr",
    "EqualsValueClause",
}


def _convert(tree: SyntaxTree) -> Node:
    if any(diagnostic.isError() for diagnostic in tree.diagnostics):
        # Fail closed for recovered parses; do not analyze a partial tree.
        raise ParseError(DiagnosticEngine.reportAll(tree.sourceManager, tree.diagnostics))
    warnings = ([DiagnosticEngine.reportAll(tree.sourceManager, tree.diagnostics)]
                if len(tree.diagnostics) else [])
    manager = tree.sourceManager

    def lower(data, native):
        if isinstance(data, list):
            result = [lower(item, obj) for item, obj in zip(data, native, strict=True)]
            return [item for item in result if item is not None]
        if "text" in data:
            if data["kind"] in DELIMITERS:
                return None
            # valueText handles escaped identifiers without their leading slash.
            return str(native.valueText) if data["kind"] == "Identifier" else data["text"]

        loc = manager.getFullyExpandedLoc(native.sourceRange.start)
        location = {"file": str(manager.getFileName(loc)),
                    "line": manager.getLineNumber(loc),
                    "column": manager.getColumnNumber(loc)}
        fields = {}
        for key, value in data.items():
            if key == "kind":
                continue
            lowered = lower(value, getattr(native, key))
            if lowered is not None:
                fields[key] = lowered
        kind = data["kind"]
        if kind in TRANSPARENT:
            if set(fields) == {"expression"}:
                return fields["expression"]
            if set(fields) <= {"equals", "expr"} and "expr" in fields:
                return fields["expr"]
        if kind == "IdentifierName":
            return Node("Name", {"name": fields["identifier"]}, location)
        if kind == "IdentifierSelectName":
            return Node("IndexName", {"name": fields["identifier"],
                                     "selectors": fields["selectors"]}, location)
        if kind == "ScopedName":
            return Node("Member" if fields["separator"] == "." else "Scope",
                        {"object": fields["left"], "member": fields["right"]}, location)
        if kind == "InvocationExpression":
            args = fields.pop("arguments", None)
            return Node("Call", {"callee": fields.pop("left"),
                                 "arguments": args.get("parameters", []) if args else [],
                                 **fields}, location)
        if kind == "ExpressionStatement":
            expression = fields.get("expr")
            if expression and expression.kind in {"Name", "IndexName", "Member", "Scope", "SystemName"}:
                # SV permits a zero-argument task invocation without (). Only
                # standalone statements have this meaning: a member appearing
                # in an expression can instead be a data/property reference.
                fields["expr"] = Node("Call", {"callee": expression, "arguments": []},
                                      expression.location)
        if kind in {"SequentialBlockStatement", "ParallelBlockStatement"}:
            result = {"body": fields.pop("items", [])}
            if kind == "ParallelBlockStatement":
                result["join"] = fields.pop("end")
                fields.pop("begin", None)
            result.update(fields)
            return Node("Sequence" if kind == "SequentialBlockStatement" else "Parallel",
                        result, location)
        if kind in {"AlwaysBlock", "InitialBlock", "FinalBlock", "AlwaysCombBlock",
                    "AlwaysFFBlock", "AlwaysLatchBlock"}:
            return Node("Process", {"process_kind": fields.pop("keyword"),
                                    "body": fields.pop("statement"), **fields}, location)
        if kind == "ModuleDeclaration":
            header = fields.pop("header")
            return Node("Module", {"name": header.get("name"),
                                   "parameters": header.get("parameters"),
                                   "ports": header.get("ports"),
                                   "body": fields.pop("members", []),
                                   **{k: v for k, v in header.fields.items()
                                      if k not in {"name", "parameters", "ports"}},
                                   **fields}, location)
        return Node(kind, fields, location)

    root = lower(json.loads(tree.root.to_json()), tree.root)
    return Node("CompilationUnit", {"members": root.get("members", [])
                if root.kind == "CompilationUnit" else [root], "warnings": warnings}, root.location)


def parse_text(source: str, filename: str = "source.sv") -> Node:
    return _convert(SyntaxTree.fromText(source, name=filename))


def parse_files(paths, include_dirs=()) -> Node:
    manager = SourceManager()
    for directory in include_dirs:
        manager.addUserDirectories(str(Path(directory).resolve()))
    resolved = [str(Path(path).resolve()) for path in paths]
    if not resolved:
        raise ValueError("at least one source file is required")
    for path in resolved:
        if not Path(path).is_file():
            raise FileNotFoundError(path)
    return _convert(SyntaxTree.fromFiles(resolved, manager))
