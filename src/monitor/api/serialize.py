"""JSON-safe serialization for API view models (Decimal → str)."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any


def to_jsonable(value: object) -> Any:
    """Recursively convert dataclasses / Decimal / Enum to JSON-ready types.

    Decimals become strings so the frontend never loses fixed-point precision.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {k: to_jsonable(v) for k, v in asdict(value).items()}
    # Fallback for plain objects with __dict__ (should not be common).
    if hasattr(value, "__dict__"):
        return {
            k: to_jsonable(v)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
    return str(value)


def to_json_dict(value: object) -> dict[str, Any]:
    """Like ``to_jsonable`` but asserts a mapping root (for FastAPI route returns)."""
    out = to_jsonable(value)
    if not isinstance(out, dict):
        raise TypeError(f"expected dict root, got {type(out)}")
    return out
