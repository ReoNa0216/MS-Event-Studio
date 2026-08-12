"""Lint user-visible Web UI copy without flagging private API field names."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Iterable


FORBIDDEN_UI_TERMS = (
    "人用",
    "machine contract",
    "SQLite",
    "snapshot",
    "快照",
    "bucket",
    "分桶",
    "EventID",
    "revision",
    "manifest",
    "stale",
    "immutable support",
)
VISIBLE_ATTRIBUTES = ("alt", "aria-label", "placeholder", "title")


class VisibleHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.fragments: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() in {"script", "style", "template"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        attributes = dict(attrs)
        for name in VISIBLE_ATTRIBUTES:
            value = attributes.get(name)
            if value:
                self.fragments.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style", "template"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.fragments.append(data)


def visible_html_copy(payload: str) -> str:
    parser = VisibleHtmlParser()
    parser.feed(payload)
    parser.close()
    return "\n".join(parser.fragments)


def find_forbidden_terms(copy: str) -> list[str]:
    findings: list[str] = []
    for term in FORBIDDEN_UI_TERMS:
        if re.search(re.escape(term), copy, flags=re.IGNORECASE):
            findings.append(term)
    return findings


def lint_html_files(paths: Iterable[Path]) -> dict[Path, list[str]]:
    failures: dict[Path, list[str]] = {}
    for path in paths:
        findings = find_forbidden_terms(visible_html_copy(path.read_text(encoding="utf-8")))
        if findings:
            failures[path] = findings
    return failures


def main(argv: list[str] | None = None) -> int:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[repository / "src/ms_event_studio/web/index.html"],
    )
    args = parser.parse_args(argv)
    missing = [path for path in args.paths if not path.is_file()]
    if missing:
        parser.error("missing UI file(s): " + ", ".join(str(path) for path in missing))
    failures = lint_html_files(args.paths)
    if failures:
        for path, terms in failures.items():
            print(f"{path}: forbidden visible UI terms: {', '.join(terms)}")
        return 1
    print(f"UI copy lint passed for {len(args.paths)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
