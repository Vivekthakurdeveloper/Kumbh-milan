"""
Validate the JSON examples in docs/api/*.md against the backend's Pydantic models.

An example is checked when the line before its ```json fence is a marker such as
    <!-- validate: app.schemas.cases:MissingPersonCreate -->
    <!-- validate: list[app.schemas.geo:NearbyCase] -->
    <!-- validate: app.schemas.common:Page[app.schemas.cases:CaseSummary] -->

Besides normal validation, keys that the model does not define are reported, because response models
ignore unknown keys and would otherwise hide invented fields.

Run from the repository root:
    backend/.venv/bin/python docs/api/tools/validate_examples.py
"""

import importlib
import json
import re
import sys
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

MARKER = re.compile(r"<!--\s*validate:\s*([\w.:\[\]]+)\s*-->\s*\n```json\n(.*?)\n```", re.DOTALL)


def resolve(spec: str):
    spec = spec.strip()
    if match := re.fullmatch(r"list\[(.+)\]", spec):
        return list[resolve(match.group(1))]
    if match := re.fullmatch(r"([\w.]+):(\w+)\[(.+)\]", spec):
        return getattr(importlib.import_module(match.group(1)), match.group(2))[resolve(match.group(3))]
    module, name = spec.split(":")
    return getattr(importlib.import_module(module), name)


def unknown_keys(example, value, path="$") -> list[str]:
    if isinstance(value, BaseModel) and isinstance(example, dict):
        problems = [f"{path}.{key}" for key in example if key not in type(value).model_fields]
        for key, item in example.items():
            if key in type(value).model_fields:
                problems += unknown_keys(item, getattr(value, key), f"{path}.{key}")
        return problems
    if isinstance(value, list) and isinstance(example, list):
        return [p for i, (e, v) in enumerate(zip(example, value)) for p in unknown_keys(e, v, f"{path}[{i}]")]
    return []


def main() -> int:
    checked = failed = 0
    for doc in sorted((ROOT / "docs" / "api").glob("*.md")):
        text = doc.read_text(encoding="utf-8")
        for match in MARKER.finditer(text):
            spec, body = match.group(1), match.group(2)
            line = text.count("\n", 0, match.start()) + 1
            checked += 1
            try:
                value = TypeAdapter(resolve(spec)).validate_json(body)
                extra = unknown_keys(json.loads(body), value)
                if extra:
                    raise ValueError(f"keys not defined by the model: {', '.join(extra)}")
            except (ValidationError, ValueError) as exc:
                failed += 1
                print(f"FAIL {doc.name}:{line} [{spec}]\n  {exc}\n")
    print(f"{checked} examples checked, {failed} failed")
    return 1 if failed or not checked else 0


if __name__ == "__main__":
    raise SystemExit(main())
