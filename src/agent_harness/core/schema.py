"""Bounded JSON Schema subset: no network references, regex or combinatorial validation."""

from jsonschema import Draft202012Validator

KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "description",
        "title",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
        "$schema",
    }
)


def validate_schema(schema):
    count = 0

    def budget(value, depth=0):
        nonlocal count
        count += 1
        if depth > 16 or count > 2048:
            raise ValueError("schema complexity limit")
        if isinstance(value, dict):
            for child in value.values():
                budget(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                budget(child, depth + 1)

    budget(schema)

    def inspect(node):
        if not isinstance(node, dict) or set(node) - KEYWORDS:
            raise ValueError("unsupported schema keyword")
        props = node.get("properties", {})
        if not isinstance(props, dict) or len(props) > 64:
            raise ValueError("schema property limit")
        if "enum" in node and (not isinstance(node["enum"], list) or len(node["enum"]) > 64):
            raise ValueError("schema enum limit")
        if "additionalProperties" in node and type(node["additionalProperties"]) is not bool:
            raise ValueError("additionalProperties must be boolean")
        for child in props.values():
            inspect(child)
        if "items" in node:
            inspect(node["items"])

    inspect(schema)
    Draft202012Validator.check_schema(schema)
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("tool schema must be a closed object")
