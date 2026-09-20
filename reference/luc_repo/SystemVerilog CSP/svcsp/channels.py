"""Conservative, module-local channel analysis over the source AST."""

from .ast import Node, children, walk

INPUT_METHODS = {"Receive", "SplitReceive", "Peek"}
OUTPUT_METHODS = {"Send", "SplitSend"}
OBSERVE_METHODS = {"Probe"}
METHODS = INPUT_METHODS | OUTPUT_METHODS | OBSERVE_METHODS


def analyze_channels(ast: Node, channel_types=("Channel",)) -> dict:
    """Return declared endpoints, directions, call evidence, and limitations.

    Generic `interface` ports are assumed to be CSP endpoints. Named interface
    types must be configured explicitly. Direction is a syntactic may-use union
    over branches, not a reachability, scheduling, or deadlock analysis.
    """
    types = set(channel_types)
    reports = []
    for module in (node for node in walk(ast) if node.kind == "Module"):
        endpoints = {}
        diagnostics = []

        def diagnostic(code, message, node):
            diagnostics.append({"code": code, "message": message, "location": node.location})

        def declare(name, scope, node, dimensions=None, modport=None):
            if name in endpoints:
                diagnostic("duplicate_endpoint", f"Duplicate endpoint {name}.", node)
                return
            endpoints[name] = {"name": name, "scope": scope, "direction": "unknown",
                               "dimensions": [x.to_dict() for x in dimensions or []],
                               "modport": modport, "location": node.location, "uses": []}

        ports = module.get("ports")
        if ports and ports.kind != "AnsiPortList":
            diagnostic("non_ansi_ports", "Channel declarations in non-ANSI ports are not resolved.", ports)
        inherited_channel = False
        inherited_modport = None
        for port in ports.get("ports", []) if ports and ports.kind == "AnsiPortList" else []:
            header = port.get("header")
            declarator = port.get("declarator")
            if not header or not declarator:
                diagnostic("unsupported_port", "This port form is not resolved.", port)
                inherited_channel = False
                continue
            data_type = header.get("dataType")
            implicit = (header.kind == "VariablePortHeader" and data_type
                        and data_type.kind == "ImplicitType"
                        and not data_type.fields and not header.get("direction")
                        and not header.get("varKeyword"))
            if not implicit:
                inherited_channel = False
                inherited_modport = None
                if header.kind == "InterfacePortHeader":
                    inherited_channel = header.get("nameOrKeyword") in types | {"interface"}
                    modport = header.get("modport")
                    inherited_modport = modport.get("member") if modport else None
                elif data_type and data_type.kind == "NamedType":
                    name = data_type.get("name")
                    inherited_channel = (name.kind == "Name" and name.get("name") in types
                                         and not header.get("direction"))
            if inherited_channel:
                declare(declarator.get("name"), "port", port,
                        declarator.get("dimensions"), inherited_modport)

        for item in module.get("body", []):
            if item.kind == "HierarchyInstantiation":
                if item.get("type") in types:
                    for instance in item.get("instances", []):
                        decl = instance.get("decl")
                        if decl:
                            declare(decl.get("name"), "internal", instance, decl.get("dimensions"))
                else:
                    diagnostic("hierarchy_not_resolved",
                               "Directions through child instances require hierarchy analysis.", item)

        def block_names(block):
            """Names that can hide a module endpoint in this lexical scope.

            This is deliberately conservative for declarations whose names need
            elaboration. In particular, a wildcard import can hide an outer
            channel even though it does not spell that channel's name locally.
            """
            names = set()

            def add_enum_names(data_type):
                if data_type and data_type.kind == "EnumType":
                    for member in data_type.get("members", []):
                        if member.get("dimensions"):
                            diagnostic("enum_names_not_elaborated",
                                       "Enum member ranges require elaboration to resolve shadowing.", member)
                            names.update(endpoints)
                        else:
                            names.add(member.get("name"))

            block_name = block.get("blockName")
            if block_name:
                names.add(block_name.get("name"))
            for item in block.get("body", []):
                if item.kind == "DataDeclaration":
                    names.update(decl.get("name") for decl in item.get("declarators", []))
                    add_enum_names(item.get("type"))
                elif item.kind in {"TypedefDeclaration", "ForwardTypedefDeclaration"}:
                    names.add(item.get("name"))
                    add_enum_names(item.get("type"))
                elif item.kind == "ParameterDeclarationStatement":
                    parameter = item.get("parameter")
                    names.update(decl.get("name") for decl in parameter.get("declarators", []))
                    add_enum_names(parameter.get("type"))
                elif item.kind == "PackageImportDeclaration":
                    for imported in item.get("items", []):
                        if imported.get("item") == "*":
                            diagnostic("wildcard_import_not_resolved",
                                       "Wildcard imports require binding to resolve channel shadowing.", imported)
                            names.update(endpoints)
                        else:
                            names.add(imported.get("item"))
                elif item.kind in {"Sequence", "Parallel"} and item.get("blockName"):
                    names.add(item.get("blockName").get("name"))
            return names

        def inspect(node, shadowed=frozenset()):
            if node.kind in {"Module", "InterfaceDeclaration", "ClassDeclaration",
                             "FunctionDeclaration", "TaskDeclaration"}:
                diagnostic("scope_not_analyzed",
                           f"{node.kind} requires a separate binding/call-graph pass.", node)
                return
            if "Generate" in node.kind:
                diagnostic("generate_not_elaborated", "Generate scopes require elaboration.", node)
                return
            if node.kind == "Sequence" or node.kind == "Parallel":
                shadowed = shadowed | block_names(node)
            if node.kind == "ForLoopStatement":
                shadowed = shadowed | {item.get("declarator").get("name")
                                       for item in node.get("initializers", [])
                                       if item.kind == "ForVariableDeclaration"}
            if node.kind == "ForeachLoopStatement":
                shadowed = shadowed | {item.get("name")
                                       for item in node.get("loopList").get("loopVariables", [])}
            if node.kind == "Call":
                callee = node.get("callee")
                if callee.kind == "Member":
                    receiver, member = callee.get("object"), callee.get("member")
                    method = member.get("name") if member.kind == "Name" else None
                    name = receiver.get("name") if receiver.kind in {"Name", "IndexName"} else None
                    endpoint = endpoints.get(name) if name not in shadowed else None
                    if endpoint and method in METHODS:
                        role = ("input" if method in INPUT_METHODS else
                                "output" if method in OUTPUT_METHODS else "observe")
                        endpoint["uses"].append({"method": method, "role": role,
                                                 "receiver": receiver.to_dict(),
                                                 "location": node.location})
                    elif endpoint:
                        diagnostic("unknown_channel_method", f"Unrecognized channel operation {method} on {name}.", node)
                    elif method in METHODS:
                        diagnostic("unresolved_receiver", "CSP-like call does not resolve to a declared endpoint in this scope.", node)
                    else:
                        diagnostic("indirect_call", "Member call may contain indirect channel operations.", node)
                elif not (callee.kind == "SystemName" or
                          (callee.kind == "Name" and callee.get("name", "").startswith("$"))):
                    diagnostic("indirect_call", "Call requires binding to rule out indirect channel operations.", node)
            for child in children(node):
                inspect(child, shadowed)

        for item in module.get("body", []):
            inspect(item)
        for endpoint in endpoints.values():
            roles = {use["role"] for use in endpoint["uses"]} - {"observe"}
            endpoint["direction"] = ("bidirectional" if len(roles) == 2 else next(iter(roles), "unknown"))
        reports.append({"module": module.get("name"), "location": module.location,
                        "analysis_complete": not diagnostics,
                        "channels": list(endpoints.values()), "diagnostics": diagnostics})
    return {"schema_version": 1, "analysis": "module-local syntactic may-use",
            "parse_warnings": ast.get("warnings", []), "modules": reports}
