"""A JSON-serializable, unelaborated AST, independent of pyslang objects."""

from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class Node:
    kind: str
    fields: dict[str, Any] = field(default_factory=dict)
    location: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default=None):
        return self.fields.get(key, default)

    def to_dict(self) -> dict:
        def encode(value):
            if isinstance(value, Node):
                return value.to_dict()
            if isinstance(value, list):
                return [encode(item) for item in value]
            return value

        return {"kind": self.kind, **{k: encode(v) for k, v in self.fields.items()},
                "location": self.location}


def children(node: Node) -> Iterator[Node]:
    for value in node.fields.values():
        if isinstance(value, Node):
            yield value
        elif isinstance(value, list):
            yield from (item for item in value if isinstance(item, Node))


def walk(node: Node) -> Iterator[Node]:
    yield node
    for child in children(node):
        yield from walk(child)
