"""Canonical JSON and content hashing helpers."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np


def json_value(value: Any) -> Any:
    if type(value).__name__ == "NAType":
        return None
    if dataclasses.is_dataclass(value):
        return json_value(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, np.ndarray):
        return [json_value(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def content_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
