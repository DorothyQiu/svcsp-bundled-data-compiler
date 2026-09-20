"""Structural Verilog emission for the validated CSP handshake IR.

The emitted top contains wires, instances and interconnect assignments only.
Datapath helper modules use synthesizable continuous expressions. Handshake
cells have separate functional models; technology mapping is an explicit step.
"""

from collections import defaultdict
from importlib.resources import files


def cell_models() -> str:
    return files("svcsp").joinpath("rtl/csp_cells.v").read_text()


class Emitter:
    def __init__(self, ir):
        self.ir = ir
        self.module = ir["name"] + "_structural"
        self.declarations = []
        self.instances = []
        self.helpers = []
        self.writers = defaultdict(list)
        self.senders = defaultdict(list)
        self.receivers = defaultdict(list)
        self.counter = 0

    def fresh(self, suffix="n"):
        self.counter += 1
        return f"__csp_{suffix}_{self.counter}"

    def wire(self, width=1, name=None):
        name = name or self.fresh()
        self.declarations.append(f"  wire [{width - 1}:0] {name};")
        return name

    def instance(self, cell, ports, parameters=None):
        params = ""
        if parameters:
            params = " #(" + ", ".join(f".{key}({value})" for key, value in parameters.items()) + ")"
        bindings = ", ".join(f".{key}({value})" for key, value in ports.items())
        self.instances.append(f"  {cell}{params} {self.fresh('u')} ({bindings});")

    def expr(self, expr, width):
        output = self.wire(width)
        helper = f"__csp_{self.module}_expr_{self.counter}"
        names = sorted(set(expr["reads"]))
        inputs = [f"input wire [{self.ir['variables'][name]['width'] - 1}:0] {name}" for name in names]
        ports = inputs + [f"output wire [{width - 1}:0] __csp_value"]
        self.helpers.append("module " + helper + " (\n  " + ",\n  ".join(ports) +
                            ");\n  assign __csp_value = " + expr["text"] + ";\nendmodule\n")
        self.instance(helper, {**{name: name for name in names}, "__csp_value": output})
        return output

    def statement(self, node, go, done):
        kind = node["kind"]
        location = node.get("location")
        if location:
            # Keep comments single-line, even for unusual source filenames.
            label = f"{location.get('file', '')}:{location.get('line', '')}".replace("\n", " ").replace("\r", " ")
            self.instances.append(f"  // {kind}: {label}")
        if kind == "skip":
            self.instances.append(f"  assign {done} = {go};")
        elif kind == "sequence":
            items = node["body"]
            if not items:
                self.instances.append(f"  assign {done} = {go};")
            for index, item in enumerate(items):
                next_done = done if index == len(items) - 1 else self.wire()
                self.statement(item, go, next_done)
                go = next_done
        elif kind == "parallel":
            branches = [self.wire() for _ in node["body"]]
            for item, branch_done in zip(node["body"], branches):
                self.statement(item, go, branch_done)
            if branches:
                self.instance("csp_join", {"reset_n": "reset_n", "go": go, "done": done,
                                          "branch_done": "{" + ", ".join(reversed(branches)) + "}"},
                              {"N": len(branches)})
            else:
                self.instances.append(f"  assign {done} = {go};")
        elif kind == "if":
            condition = self.expr(node["condition"], node["condition"]["width"])
            then_go, then_done, else_go, else_done = [self.wire() for _ in range(4)]
            self.instance("csp_branch", {"reset_n": "reset_n", "go": go, "done": done,
                                         "condition": f"(|{condition})", "then_go": then_go,
                                         "then_done": then_done, "else_go": else_go, "else_done": else_done})
            self.statement(node["then"], then_go, then_done)
            self.statement(node["else"], else_go, else_done)
        elif kind == "send":
            channel = node["channel"]
            width = self.ir["channels"][channel]["width"]
            data_in = self.expr(node["expr"], width)
            data, request = self.wire(width), self.wire()
            self.instance("csp_send", {"reset_n": "reset_n", "go": go, "done": done,
                                       "data_in": data_in, "channel_data": data,
                                       "channel_req": request, "channel_ack": channel + "_ack"}, {"WIDTH": width})
            self.senders[channel].append((data, request))
        elif kind in {"receive", "assign"}:
            target = node["target"]
            width = self.ir["variables"][target]["width"]
            data, enable = self.wire(width), self.wire()
            ports = {"reset_n": "reset_n", "go": go, "done": done,
                     "write_data": data, "write_enable": enable}
            if kind == "receive":
                channel = node["channel"]
                ack = self.wire()
                ports.update(channel_data=channel + "_data", channel_req=channel + "_req", channel_ack=ack)
                self.receivers[channel].append(ack)
            else:
                ports["data_in"] = self.expr(node["expr"], width)
            self.instance("csp_receive" if kind == "receive" else "csp_assign", ports, {"WIDTH": width})
            self.writers[target].append((data, enable))
        else:
            raise ValueError(f"unsupported IR statement: {kind}")

    def emit(self):
        names = {"reset_n"}
        ports = ["input wire reset_n"]
        for name, channel in self.ir["channels"].items():
            direction = channel["direction"]
            inverse = "output" if direction == "input" else "input"
            for suffix in ("data", "req", "ack"):
                flattened = name + "_" + suffix
                if flattened in names or flattened in self.ir["variables"]:
                    raise ValueError(f"flattened channel port name collides with another symbol: {flattened}")
                names.add(flattened)
            ports.extend([f"{direction} wire [{channel['width'] - 1}:0] {name}_data",
                          f"{direction} wire {name}_req", f"{inverse} wire {name}_ack"])
        for name, variable in self.ir["variables"].items():
            self.wire(variable["width"], name)
        go, done = self.wire(name="__csp_go"), self.wire(name="__csp_done")
        self.instance("csp_loop", {"reset_n": "reset_n", "body_go": go, "body_done": done})
        self.statement(self.ir["body"], go, done)
        for name, variable in self.ir["variables"].items():
            writes = self.writers[name]
            enable = " | ".join(item[1] for item in writes) or "1'b0"
            width = variable["width"]
            mux = " | ".join(f"({{{width}{{{en}}}}} & {data})" for data, en in writes) or f"{width}'d0"
            self.instance("csp_storage", {"reset_n": "reset_n", "write_enable": f"({enable})",
                                          "write_data": f"({mux})", "value": name},
                          {"WIDTH": width, "INITIAL": f"{width}'d{variable['initial']}"})
        for channel, senders in self.senders.items():
            self.instance("csp_send_mux", {"reset_n": "reset_n",
                                           "requests": "{" + ", ".join(req for _, req in reversed(senders)) + "}",
                                           "data": "{" + ", ".join(data for data, _ in reversed(senders)) + "}",
                                           "channel_req": channel + "_req", "channel_data": channel + "_data",
                                           "channel_ack": channel + "_ack"},
                          {"WIDTH": self.ir["channels"][channel]["width"], "N": len(senders)})
        for channel, receivers in self.receivers.items():
            self.instances.append(f"  assign {channel}_ack = " + " | ".join(receivers) + ";")
        header = ["// Generated by svcsp: four-phase bundled-data structural netlist.",
                  "// No global clock. Assert reset_n low before starting traffic.",
                  "// Handshake cells need technology mapping; csp_cells.v provides functional models.",
                  "`timescale 1ns/1ps", "`default_nettype none",
                  f"module {self.module} (\n  " + ",\n  ".join(ports) + ");"]
        return "\n".join(header + self.declarations + [""] + self.instances +
                         ["endmodule", "", *self.helpers, "`default_nettype wire", ""])


def emit_verilog(ir: dict) -> str:
    return Emitter(ir).emit()
