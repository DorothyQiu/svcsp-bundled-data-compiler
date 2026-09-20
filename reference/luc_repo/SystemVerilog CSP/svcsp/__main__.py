import argparse
import json
import sys
from pathlib import Path

from .channels import analyze_channels
from .parser import ParseError, parse_files


def main(argv=None):
    parser = argparse.ArgumentParser(description="SystemVerilog CSP front end")
    parser.add_argument("command", choices=["ast", "channels", "ir", "compile"])
    parser.add_argument("files", nargs="+")
    parser.add_argument("-I", "--include-dir", action="append", default=[])
    parser.add_argument("--channel-type", action="append", default=[],
                        help="additional CSP interface type (Channel is always included)")
    parser.add_argument("--strict", action="store_true",
                        help="exit 2 if channel analysis is incomplete")
    parser.add_argument("--top", help="source module to compile (required when more than one exists)")
    parser.add_argument("-P", "--parameter", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("-W", "--channel-width", action="append", default=[], metavar="NAME=WIDTH")
    parser.add_argument("-o", "--output", type=Path, help="write output to a file instead of stdout")
    parser.add_argument("--with-models", action="store_true",
                        help="append functional simulation cell models to compiled Verilog")
    args = parser.parse_args(argv)
    try:
        ast = parse_files(args.files, args.include_dir)
        if args.command in {"ir", "compile"}:
            from .lowering import lower
            from .backend import emit_verilog, cell_models

            def bindings(values):
                result = {}
                for value in values:
                    name, sep, literal = value.partition("=")
                    if not sep or not name or name in result:
                        raise ValueError(f"expected unique NAME=VALUE, got {value!r}")
                    result[name] = int(literal, 0)
                return result

            ir = lower(ast, top=args.top, parameters=bindings(args.parameter),
                       channel_widths=bindings(args.channel_width))
            if args.command == "ir":
                output = json.dumps({"schema_version": 1, "stage": "four-phase-handshake-ir", "ir": ir}, indent=2) + "\n"
            else:
                output = emit_verilog(ir)
                if args.with_models:
                    output += "\n" + cell_models()
        elif args.command == "ast":
            result = {"schema_version": 1, "stage": "unelaborated-source-ast", "ast": ast.to_dict()}
            output = json.dumps(result, indent=2) + "\n"
        else:
            result = analyze_channels(ast, ["Channel", *args.channel_type])
            output = json.dumps(result, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output)
        else:
            print(output, end="")
        if args.command == "channels" and args.strict:
            return 2 if any(not item["analysis_complete"] for item in result["modules"]) else 0
        return 0
    except (ParseError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
