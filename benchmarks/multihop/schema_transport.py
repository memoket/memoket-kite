"""Explicit transport adaptation for annotated local schema references.

This changes schema representation only. It is not applied automatically to
Compile/Extract calls and never changes generated records or source text.
"""

from copy import deepcopy


def inline_annotated_refs(schema: dict) -> dict:
    """Inline local refs with descriptions, preserving constraints and prose.

    Some strict endpoints reject description as a sibling of $ref. Pure refs
    remain shared. Ref siblings with validation semantics are refused rather
    than merged, because merging them need not preserve JSON Schema meaning.
    """
    original = deepcopy(schema)

    def resolve(reference):
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise ValueError("annotated references must resolve locally")
        value = original
        for key in reference[2:].split("/"):
            value = value[key.replace("~1", "/").replace("~0", "~")]
        if not isinstance(value, dict):
            raise ValueError("schema reference must resolve to an object")
        return value

    def visit(node, expanding=()):
        if isinstance(node, list):
            return [visit(value, expanding) for value in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node and len(node) > 1:
            if set(node) - {"$ref", "description"}:
                raise ValueError("cannot inline reference with semantic siblings")
            reference = node["$ref"]
            if reference in expanding:
                raise ValueError("cannot inline cyclic annotated references")
            target = deepcopy(resolve(reference))
            target["description"] = node["description"]
            return visit(target, expanding + (reference,))
        return {key: visit(value, expanding) for key, value in node.items()}

    return visit(original)
