from __future__ import annotations


def dump_yaml(data: object, indent: int = 0) -> str:
    """Serialize a small subset of Python data structures to YAML."""
    if isinstance(data, dict):
        lines: list[str] = []
        for key, value in data.items():
            prefix = " " * indent + f"{key}:"
            if _is_scalar(value):
                lines.append(f"{prefix} {_format_scalar(value)}")
            elif value == []:
                lines.append(f"{prefix} []")
            elif value == {}:
                lines.append(f"{prefix} {{}}")
            else:
                lines.append(prefix)
                lines.append(dump_yaml(value, indent + 2))
        return "\n".join(lines)
    if isinstance(data, list):
        lines = []
        for item in data:
            prefix = " " * indent + "-"
            if _is_scalar(item):
                lines.append(f"{prefix} {_format_scalar(item)}")
            else:
                lines.append(prefix)
                lines.append(dump_yaml(item, indent + 2))
        return "\n".join(lines)
    return " " * indent + _format_scalar(data)


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _format_scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if any(ch in text for ch in [":", "#", "{", "}", "[", "]", "\n", '"', "'"]):
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text
