"""Fail-closed semantic lowering for the first bundled-data CSP backend.

This accepts a documented, flat synthesis subset; it is not an elaborator for
arbitrary SystemVerilog. The source AST remains independent of this compiler IR.
"""

from dataclasses import dataclass, field
import re

from pyslang import SVInt
from pyslang.ast import ScriptSession
from pyslang.syntax import SyntaxTree

from .ast import Node


class CompileError(ValueError):
    """A source-located unsupported construct or semantic error."""


MAX_WIDTH = 65536
MAX_STATEMENTS = 10000
_BINARY = {
    "AddExpression": "+", "SubtractExpression": "-", "MultiplyExpression": "*",
    "DivideExpression": "/", "ModExpression": "%", "BinaryAndExpression": "&",
    "BinaryOrExpression": "|", "BinaryXorExpression": "^", "BinaryXnorExpression": "~^",
    "LogicalAndExpression": "&&", "LogicalOrExpression": "||",
    "EqualityExpression": "==", "InequalityExpression": "!=",
    "LessThanExpression": "<", "LessThanEqualExpression": "<=",
    "GreaterThanExpression": ">", "GreaterThanEqualExpression": ">=",
    "LogicalShiftLeftExpression": "<<", "LogicalShiftRightExpression": ">>",
    "ArithmeticShiftLeftExpression": "<<<", "ArithmeticShiftRightExpression": ">>>",
}
_UNARY = {
    "UnaryPlusExpression": "+", "UnaryMinusExpression": "-",
    "UnaryBitwiseNotExpression": "~", "UnaryLogicalNotExpression": "!",
    "UnaryBitwiseAndExpression": "&", "UnaryBitwiseNandExpression": "~&",
    "UnaryBitwiseOrExpression": "|", "UnaryBitwiseNorExpression": "~|",
    "UnaryBitwiseXorExpression": "^", "UnaryBitwiseXnorExpression": "~^",
}


def _fail(node, message):
    loc = node.location if isinstance(node, Node) else {}
    raise CompileError(f"{loc.get('file', '<source>')}:{loc.get('line', 1)}:"
                       f"{loc.get('column', 1)}: {message}")


def _check(node, kind, fields):
    if not isinstance(node, Node) or node.kind not in ({kind} if isinstance(kind, str) else kind):
        _fail(node, f"unsupported construct {getattr(node, 'kind', repr(node))}; expected {kind}")
    extra = set(node.fields) - set(fields)
    if extra:
        _fail(node, f"unsupported {node.kind} fields: {', '.join(sorted(extra))}")


@dataclass
class _Effects:
    reads: set = field(default_factory=set)
    writes: set = field(default_factory=set)
    channels: set = field(default_factory=set)

    def merge(self, other):
        self.reads |= other.reads
        self.writes |= other.writes
        self.channels |= other.channels


class _Lowerer:
    def __init__(self, module, parameters, widths):
        self.module = module
        self.overrides = dict(parameters or {})
        self.width_overrides = dict(widths or {})
        self.parameters = {}
        self.parameter_text = {}
        self.variables = {}
        self.channels = {}
        self.port_nodes = {}
        self.names = set()
        self.initialized = set()
        self.remaining = MAX_STATEMENTS

    def declare(self, name, node):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9$]*", name):
            _fail(node, "only ordinary unescaped identifiers are supported")
        if name == "reset_n" or name.startswith("__csp_"):
            _fail(node, f"reserved compiler identifier {name}")
        # Escaped SV identifiers lose their escape marker in the source AST.
        # A normalized keyword (e.g. \\wire ) cannot be emitted unescaped.
        check = SyntaxTree.fromText(f"module __csp_check; logic {name}; endmodule")
        if any(d.isError() for d in check.diagnostics):
            _fail(node, f"identifier {name} cannot be emitted as an ordinary Verilog name")
        if name in self.names:
            _fail(node, f"duplicate declaration {name}")
        self.names.add(name)

    def evaluate(self, text, reads, node, context=None, constant=False):
        """Let the pinned SV evaluator handle sizing, signedness and overflow."""
        session = ScriptSession()
        for name in sorted(reads):
            session.eval(f"localparam logic [{self.variables[name]['width']-1}:0] {name}=0;")
        if context:
            session.eval(f"localparam {context} __csp_value = {text};")
            value = session.eval("__csp_value")
        else:
            value = session.eval(text)
        if any(d.isError() for d in session.getDiagnostics()) or not isinstance(value.value, SVInt):
            _fail(node, f"invalid integral expression {text}")
        if constant and value.hasUnknown():
            _fail(node, "constant expression has unknown value (possibly division by zero)")
        if not 1 <= value.value.bitWidth <= MAX_WIDTH:
            _fail(node, f"expression width must be between 1 and {MAX_WIDTH}")
        return value.value

    def constant(self, node, context=None):
        text, reads = self.render(node)
        if reads:
            _fail(node, "constant expression must depend only on parameters and literals")
        return self.evaluate(text, reads, node, context, constant=True)

    def predicate(self, node):
        _check(node, "ConditionalPredicate", {"conditions"})
        conditions = node.get("conditions", [])
        if len(conditions) != 1:
            _fail(node, "only a single ordinary condition is supported")
        _check(conditions[0], "ConditionalPattern", {"expr"})
        return conditions[0].get("expr")

    def render(self, node):
        """Validate every expression node and retain Verilog evaluation context."""
        if not isinstance(node, Node):
            _fail(node, "missing expression")
        if node.kind == "Name":
            _check(node, "Name", {"name"})
            name = node.get("name")
            if name in self.parameter_text:
                return self.parameter_text[name], set()
            if name in self.variables:
                return name, {name}
            _fail(node, f"unbound data name {name}")
        if node.kind == "IntegerLiteralExpression":
            _check(node, node.kind, {"literal"})
            text = node.get("literal", "").replace("_", "")
            if not re.fullmatch(r"[0-9]+", text):
                _fail(node, "only known integer literals are supported")
            return text, set()
        if node.kind == "IntegerVectorExpression":
            _check(node, node.kind, {"size", "base", "value"})
            size = node.get("size", "").replace("_", "")
            base = node.get("base", "").lower()
            digits = node.get("value", "").replace("_", "")
            if not size or not size.isdigit() or not 1 <= int(size) <= MAX_WIDTH:
                _fail(node, f"based literals require an explicit width from 1 to {MAX_WIDTH}")
            if not re.fullmatch(r"'s?[bodh]", base) or re.search(r"[xXzZ?]", digits):
                _fail(node, "unknown/high-impedance literal values are not supported")
            return size + base + digits, set()
        if node.kind == "UnbasedUnsizedLiteralExpression":
            _fail(node, "unbased unsized literals ('0/'1) are unsupported; use an explicitly sized literal")
        if node.kind in _BINARY:
            _check(node, node.kind, {"left", "right", "operatorToken"})
            lhs, lr = self.render(node.get("left"))
            rhs, rr = self.render(node.get("right"))
            op = node.get("operatorToken")
            if op != _BINARY[node.kind] and not (node.kind == "BinaryXnorExpression" and op == "^~"):
                _fail(node, "unsupported binary operator")
            return f"({lhs} {op} {rhs})", lr | rr
        if node.kind in _UNARY:
            _check(node, node.kind, {"operand", "operatorToken"})
            operand, reads = self.render(node.get("operand"))
            op = node.get("operatorToken")
            if op != _UNARY[node.kind] and not (node.kind == "UnaryBitwiseXnorExpression" and op == "^~"):
                _fail(node, "unsupported unary operator")
            return f"({op}{operand})", reads
        if node.kind == "ConcatenationExpression":
            _check(node, node.kind, {"expressions"})
            parts = [self.render(n) for n in node.get("expressions", [])]
            if not parts:
                _fail(node, "empty concatenation")
            return "{" + ", ".join(p[0] for p in parts) + "}", set().union(*(p[1] for p in parts))
        if node.kind == "MultipleConcatenationExpression":
            _check(node, node.kind, {"expression", "concatenation"})
            count = int(self.constant(node.get("expression")))
            if not 1 <= count <= MAX_WIDTH:
                _fail(node, f"replication count must be between 1 and {MAX_WIDTH}")
            text, reads = self.render(node.get("concatenation"))
            return "{" + str(count) + text + "}", reads
        if node.kind == "ConditionalExpression":
            _check(node, node.kind, {"predicate", "question", "left", "colon", "right"})
            condition, cr = self.render(self.predicate(node.get("predicate")))
            lhs, lr = self.render(node.get("left"))
            rhs, rr = self.render(node.get("right"))
            return f"({condition} ? {lhs} : {rhs})", cr | lr | rr
        if node.kind == "IndexName":
            _check(node, node.kind, {"name", "selectors"})
            name = node.get("name")
            if name not in self.variables:
                _fail(node, "constant selects are supported only on declared data variables")
            selectors = node.get("selectors", [])
            if len(selectors) != 1:
                _fail(node, "only one packed select is supported")
            _check(selectors[0], "ElementSelect", {"selector"})
            select = selectors[0].get("selector")
            if select.kind == "BitSelect":
                _check(select, "BitSelect", {"expr"})
                high = low = int(self.constant(select.get("expr")))
                suffix = str(high)
            else:
                _check(select, "SimpleRangeSelect", {"left", "right", "range"})
                high, low = int(self.constant(select.get("left"))), int(self.constant(select.get("right")))
                suffix = f"{high}:{low}"
            if not 0 <= low <= high < self.variables[name]["width"]:
                _fail(node, f"select on {name} must be descending and within its declared width")
            return f"{name}[{suffix}]", {name}
        _fail(node, f"unsupported expression {node.kind}")

    def expression(self, node, initialized):
        text, reads = self.render(node)
        missing = reads - initialized
        if missing:
            _fail(node, f"read before definite initialization: {', '.join(sorted(missing))}")
        width = self.evaluate(text, reads, node, constant=not reads).bitWidth
        return {"text": text, "width": width, "reads": sorted(reads)}

    def width(self, type_node):
        _check(type_node, {"LogicType", "RegType", "BitType"}, {"keyword", "signing", "dimensions"})
        if type_node.get("signing", "unsigned") != "unsigned":
            _fail(type_node, "signed data variables are unsupported")
        dimensions = type_node.get("dimensions", [])
        if not dimensions:
            return 1
        if len(dimensions) != 1:
            _fail(type_node, "only one packed [N:0] dimension is supported")
        dimension = dimensions[0]
        _check(dimension, "VariableDimension", {"specifier"})
        specifier = dimension.get("specifier")
        _check(specifier, "RangeDimensionSpecifier", {"selector"})
        selector = specifier.get("selector")
        _check(selector, "SimpleRangeSelect", {"left", "range", "right"})
        high = int(self.constant(selector.get("left")))
        low = int(self.constant(selector.get("right")))
        if low != 0 or not 0 <= high < MAX_WIDTH:
            _fail(type_node, f"packed ranges must be [N:0], with width from 1 to {MAX_WIDTH}")
        return high + 1

    def parameter(self, node):
        _check(node, "ParameterDeclaration", {"keyword", "type", "declarators"})
        type_node = node.get("type")
        if type_node.kind in {"IntType", "IntegerType"}:
            _check(type_node, type_node.kind, {"keyword", "signing"})
            context = "int" + (" unsigned" if type_node.get("signing") == "unsigned" else "")
        elif type_node.kind == "ImplicitType" and not type_node.fields:
            context = None
        else:
            width = self.width(type_node)
            context = f"logic [{width-1}:0]"
        for decl in node.get("declarators", []):
            _check(decl, "Declarator", {"name", "initializer"})
            name = decl.get("name")
            self.declare(name, decl)
            initializer = decl.get("initializer")
            if initializer is None:
                _fail(decl, f"parameter {name} requires a constant default")
            # Validate even overridden defaults, so unsupported syntax never disappears.
            default = self.constant(initializer, context)
            if name in self.overrides:
                if node.get("keyword") != "parameter":
                    _fail(decl, f"localparam {name} cannot be overridden")
                override = self.overrides.pop(name)
                if isinstance(override, bool) or not isinstance(override, int):
                    _fail(decl, f"parameter override {name} must be an integer")
                value = self.evaluate(str(override), set(), decl, context, constant=True)
            else:
                value = default
            self.parameters[name] = int(value)
            number, signed, width = int(value), value.isSigned, value.bitWidth
            sign = "s" if signed else ""
            literal = f"{width}'{sign}d{abs(number)}"
            self.parameter_text[name] = f"(-{literal})" if number < 0 else literal

    def ports(self, node):
        _check(node, "AnsiPortList", {"ports"})
        inherited = False
        for port in node.get("ports", []):
            _check(port, "ImplicitAnsiPort", {"header", "declarator"})
            header = port.get("header")
            if header.kind == "InterfacePortHeader":
                _check(header, header.kind, {"nameOrKeyword"})
                inherited = header.get("nameOrKeyword") in {"interface", "Channel"}
            else:
                _check(header, "VariablePortHeader", {"dataType"})
                dt = header.get("dataType")
                if dt.kind == "NamedType":
                    _check(dt, "NamedType", {"name"})
                    _check(dt.get("name"), "Name", {"name"})
                    inherited = dt.get("name").get("name") == "Channel"
                elif dt.kind == "ImplicitType" and not dt.fields:
                    pass
                else:
                    inherited = False
            if not inherited:
                _fail(port, "only generic interface or Channel ANSI channel ports are supported")
            decl = port.get("declarator")
            _check(decl, "Declarator", {"name"})
            name = decl.get("name")
            self.declare(name, decl)
            self.port_nodes[name] = port
        for name, width in self.width_overrides.items():
            if name not in self.port_nodes:
                _fail(node, f"unknown channel width override {name}")
            if isinstance(width, bool) or not isinstance(width, int) or not 1 <= width <= MAX_WIDTH:
                _fail(node, f"channel width {name} must be an integer from 1 to {MAX_WIDTH}")

    def channel(self, name, direction, width, node):
        if name not in self.port_nodes:
            _fail(node, f"unbound channel {name}")
        declared_width = self.width_overrides.get(name, width)
        if direction == "input" and declared_width != width:
            _fail(node, f"receive destination width {width} differs from channel {name} width {declared_width}")
        old = self.channels.get(name)
        if old and old["direction"] != direction:
            _fail(node, f"bidirectional channel {name} is unsupported")
        if old and old["width"] != declared_width:
            _fail(node, f"inconsistent inferred widths for channel {name}; supply an explicit channel width")
        self.channels[name] = {"direction": direction, "width": declared_width}

    def target(self, node):
        _check(node, "Name", {"name"})
        name = node.get("name")
        if name not in self.variables:
            _fail(node, f"assignment/receive target {name} must be a declared whole data variable")
        return name

    def statement(self, node, initialized):
        ir, after, effects, communication = self._statement(node, initialized)
        ir["location"] = dict(node.location)
        return ir, after, effects, communication

    def _statement(self, node, initialized):
        self.remaining -= 1
        if self.remaining < 0:
            _fail(node, f"unrolled body exceeds {MAX_STATEMENTS} statements")
        initialized = set(initialized)
        effects = _Effects()
        if node.kind in {"Sequence", "Parallel"}:
            _check(node, node.kind, {"body", "join"} if node.kind == "Parallel" else {"body"})
            if node.kind == "Parallel" and node.get("join") != "join":
                _fail(node, "only fork...join is supported")
            body, communication = [], False
            outputs = set(initialized)
            for child in node.get("body", []):
                ir, after, child_effects, comm = self.statement(child, initialized)
                if node.kind == "Parallel":
                    conflict = ((effects.writes & (child_effects.reads | child_effects.writes))
                                | (effects.reads & child_effects.writes))
                    channel_conflict = effects.channels & child_effects.channels
                    if conflict or channel_conflict:
                        _fail(child, "parallel branch conflict on " + ", ".join(sorted(conflict | channel_conflict)))
                    outputs |= after
                else:
                    initialized = after
                effects.merge(child_effects)
                body.append(ir)
                communication |= comm
            ir = {"kind": "parallel" if node.kind == "Parallel" else "sequence", "body": body}
            return ir, outputs if node.kind == "Parallel" else initialized, effects, communication
        if node.kind == "EmptyStatement":
            _check(node, node.kind, set())
            return {"kind": "skip"}, initialized, effects, False
        if node.kind == "ExpressionStatement":
            _check(node, node.kind, {"expr"})
            expr = node.get("expr")
            if expr.kind == "AssignmentExpression":
                _check(expr, expr.kind, {"left", "operatorToken", "right"})
                if expr.get("operatorToken") != "=":
                    _fail(expr, "only blocking '=' assignment is supported")
                target = self.target(expr.get("left"))
                value = self.expression(expr.get("right"), initialized)
                effects.reads |= set(value["reads"])
                effects.writes.add(target)
                initialized.add(target)
                return {"kind": "assign", "target": target, "expr": value}, initialized, effects, False
            _check(expr, "Call", {"callee", "arguments"})
            callee = expr.get("callee")
            _check(callee, "Member", {"object", "member"})
            _check(callee.get("object"), "Name", {"name"})
            _check(callee.get("member"), "Name", {"name"})
            name, method = callee.get("object").get("name"), callee.get("member").get("name")
            arguments = expr.get("arguments", [])
            if len(arguments) != 1:
                _fail(expr, "Send/Receive require exactly one positional argument")
            _check(arguments[0], "OrderedArgument", {"expr"})
            argument = arguments[0].get("expr")
            effects.channels.add(name)
            if method == "Receive":
                target = self.target(argument)
                self.channel(name, "input", self.variables[target]["width"], expr)
                initialized.add(target)
                effects.writes.add(target)
                return {"kind": "receive", "channel": name, "target": target}, initialized, effects, True
            if method == "Send":
                value = self.expression(argument, initialized)
                self.channel(name, "output", value["width"], expr)
                effects.reads |= set(value["reads"])
                return {"kind": "send", "channel": name, "expr": value}, initialized, effects, True
            _fail(expr, f"unsupported channel method {method}; only Send and Receive are supported")
        if node.kind == "ConditionalStatement":
            _check(node, node.kind, {"ifKeyword", "predicate", "statement", "elseClause"})
            condition = self.expression(self.predicate(node.get("predicate")), initialized)
            yes, yes_after, yes_effects, yes_comm = self.statement(node.get("statement"), initialized)
            otherwise = node.get("elseClause")
            if otherwise:
                _check(otherwise, "ElseClause", {"elseKeyword", "clause"})
                no, no_after, no_effects, no_comm = self.statement(otherwise.get("clause"), initialized)
            else:
                no, no_after, no_effects, no_comm = {"kind": "skip"}, initialized, _Effects(), False
            effects.reads |= set(condition["reads"])
            effects.merge(yes_effects)
            effects.merge(no_effects)
            return {"kind": "if", "condition": condition, "then": yes, "else": no}, yes_after & no_after, effects, yes_comm and no_comm
        if node.kind == "LoopStatement":
            _check(node, node.kind, {"repeatOrWhile", "expr", "statement"})
            if node.get("repeatOrWhile") != "repeat":
                _fail(node, "only constant repeat loops are supported inside always")
            count = int(self.constant(node.get("expr")))
            if not 0 <= count <= 256:
                _fail(node, "repeat count must be between 0 and 256")
            if count == 0:
                snapshot = dict(self.channels)
                self.statement(node.get("statement"), initialized)
                self.channels = snapshot
                return {"kind": "skip"}, initialized, effects, False
            body, communication = [], False
            for _ in range(count):
                child, initialized, child_effects, comm = self.statement(node.get("statement"), initialized)
                body.append(child)
                effects.merge(child_effects)
                communication |= comm
            return {"kind": "sequence", "body": body}, initialized, effects, communication
        _fail(node, f"unsupported statement {node.kind}")

    def run(self):
        module = self.module
        _check(module, "Module", {"name", "parameters", "ports", "body"})
        name = module.get("name")
        # Validate module names with the same backend identifier restrictions.
        self.declare(name, module)
        self.names.remove(name)
        params = module.get("parameters")
        if params:
            _check(params, "ParameterPortList", {"hash", "declarations"})
            for declaration in params.get("declarations", []):
                self.parameter(declaration)
        for item in module.get("body", []):
            if item.kind == "ParameterDeclarationStatement":
                _check(item, item.kind, {"parameter"})
                self.parameter(item.get("parameter"))
        if self.overrides:
            _fail(module, "unknown parameter override(s): " + ", ".join(sorted(self.overrides)))
        self.ports(module.get("ports"))
        processes = []
        for item in module.get("body", []):
            if item.kind == "ParameterDeclarationStatement":
                continue
            if item.kind == "Process":
                _check(item, "Process", {"process_kind", "body"})
                if item.get("process_kind") != "always":
                    _fail(item, "only an ordinary always process is supported")
                processes.append(item)
                continue
            _check(item, "DataDeclaration", {"type", "declarators"})
            width = self.width(item.get("type"))
            for decl in item.get("declarators", []):
                _check(decl, "Declarator", {"name", "initializer"})
                variable = decl.get("name")
                self.declare(variable, decl)
                initializer = decl.get("initializer")
                initial = int(self.constant(initializer, f"logic [{width-1}:0]")) if initializer else 0
                self.variables[variable] = {"width": width, "initial": initial}
                if initializer or item.get("type").kind == "BitType":
                    self.initialized.add(variable)
        if len(processes) != 1:
            _fail(module, "exactly one ordinary always process is required")
        flattened = {f"{channel}_{suffix}" for channel in self.port_nodes for suffix in ("data", "req", "ack")}
        collisions = flattened & self.names
        if collisions:
            _fail(module, "flattened channel port name collision: " + ", ".join(sorted(collisions)))
        body, _, _, communication = self.statement(processes[0].get("body"), self.initialized)
        if not communication:
            _fail(processes[0], "every always iteration path must contain a Send or Receive")
        unused = set(self.port_nodes) - set(self.channels)
        if unused:
            _fail(self.port_nodes[sorted(unused)[0]], "unused channel port(s): " + ", ".join(sorted(unused)))
        return {"name": name, "parameters": self.parameters, "variables": self.variables,
                "channels": {n: self.channels[n] for n in self.port_nodes}, "body": body}


def lower(ast: Node, top: str | None = None, parameters: dict[str, int] | None = None,
          channel_widths: dict[str, int] | None = None) -> dict:
    """Bind and validate a selected module, returning JSON-serializable compiler IR."""
    _check(ast, "CompilationUnit", {"members", "warnings"})
    modules = {}
    for member in ast.get("members", []):
        if member.kind != "Module":
            _fail(member, f"unsupported compilation-unit declaration {member.kind}")
        name = member.get("name")
        if name in modules:
            _fail(member, f"duplicate module {name}")
        modules[name] = member
    if top is None:
        if len(modules) != 1:
            _fail(ast, "select a top module explicitly when the source does not contain exactly one module")
        top = next(iter(modules))
    if top not in modules:
        _fail(ast, f"unknown top module {top}")
    return _Lowerer(modules[top], parameters, channel_widths).run()
