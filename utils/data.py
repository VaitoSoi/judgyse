from datetime import datetime
import typing

def str_to_timestamp(s: str) -> float:
    s = s[:26]
    if s.endswith("Z"):
        s = s[:-1]
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f").timestamp()


def padding(arr: tuple | list, length: int, fill: typing.Any = None):
    return arr + ([fill] if isinstance(arr, list) else (fill,)) * (length - len(arr))


def mem_convert(mem: str) -> int:
    mem = mem.upper()
    if mem.endswith("K"):
        return int(mem[:-1]) * 1024
    if mem.endswith("M"):
        return int(mem[:-1]) * 1024 ** 2
    if mem.endswith("G"):
        return int(mem[:-1]) * 1024 ** 3
    raise ValueError(f"Unknown memory unit: {mem}")


def wrap_dict(key_val: list[tuple[str, str]]) -> dict[str, str]:
    return {key: val for key, val in key_val}
