import json
import os
import typing

json_indent = os.getenv("ENV", "development") == "development" and 4 or None


def read(file: str) -> str:
    return open(file, "r").read()


def write(file: str, content: str) -> None:
    return open(file, "w").write(content)


def read_json(file: str) -> dict[str, typing.Any]:
    return json.loads(read(file))


def write_json(
    file: str, content: dict[str, typing.Any], indent=json_indent
) -> None:
    return write(file, json.dumps(content, indent=indent))
