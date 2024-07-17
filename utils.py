import typing
import json
import os
import shutil
import pydantic
from datetime import datetime

__all__ = ["read", "write", "read_json", "write_json"]

json_indent = os.getenv("ENV", "development") == "development" and 4 or None


def read(file: str) -> str:
    return open(file, "r").read()


def write(file: str, content: str) -> None:
    return open(file, "w").write(content)


def read_json(file: str) -> typing.Dict[str, typing.Any]:
    return json.loads(read(file))


def write_json(
    file: str, content: typing.Dict[str, typing.Any], indent=json_indent
) -> None:
    return write(file, json.dumps(content, indent=indent))


def remove_content(folder: str, exclude: typing.List[str] = []) -> None:
    for item in os.listdir(folder):
        if item in exclude:
            continue
        path = os.path.join(folder, item)
        if os.path.isfile(path):
            os.unlink(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        else:  # never happend :D
            raise LookupError(f"type of {path} is unknown")


def str_to_timestamp(s: str) -> float:
    return datetime.strptime(s[:26], "%Y-%m-%dT%H:%M:%S.%f").timestamp()


def get_fields(
    model: pydantic.BaseModel,
) -> typing.Tuple[typing.List[str], typing.List[str]]:
    strict = []
    optional = []
    for name, field in model.model_fields.items():
        if field.is_required():
            strict.append(name)
        else:
            optional.append(name)

    return strict, optional
