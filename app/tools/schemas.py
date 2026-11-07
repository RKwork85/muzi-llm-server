"""把 Pydantic 参数模型转成 DeepSeek function parameters。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def parameters_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema.pop("description", None)
    schema.pop("$schema", None)
    schema["additionalProperties"] = False
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema
