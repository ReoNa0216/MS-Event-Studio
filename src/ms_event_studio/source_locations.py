"""Local source-file hints; never part of a portable scientific project.

Only callers that have verified the complete source fingerprint may remember a
location. A hint is not proof of identity: extraction still checks every byte.
WebSession serializes access to this small, disposable per-user cache.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid


class SourceLocations:
    def __init__(self, path: Path):
        self.path = path
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        self.locations = {key: value for key, value in data.items()
                          if isinstance(key, str) and isinstance(value, str)} if isinstance(data, dict) else {}

    def get(self, fingerprint: str) -> Path | None:
        value = self.locations.get(fingerprint)
        return Path(value) if value else None

    def remember(self, fingerprint: str, source: Path) -> None:
        self.locations[fingerprint] = str(source.resolve())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f'.{self.path.name}.writing-{uuid.uuid4().hex}')
        try:
            temporary.write_text(json.dumps(self.locations, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
