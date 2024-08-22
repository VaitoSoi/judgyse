import pydantic
import typing

def get_fields(
    model: pydantic.BaseModel,
) -> typing.Tuple[list[str], list[str]]:
    strict = []
    optional = []
    for name, field in model.model_fields.items():
        if field.is_required():
            strict.append(name)
        else:
            optional.append(name)

    return strict, optional