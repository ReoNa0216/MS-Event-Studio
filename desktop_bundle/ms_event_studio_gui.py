from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


def _smoke_report_path() -> Path | None:
    try:
        index = sys.argv.index("--smoke-report")
        return Path(sys.argv[index + 1])
    except (ValueError, IndexError):
        return None


try:
    from ms_event_studio.desktop import main
except BaseException as exc:
    report = _smoke_report_path()
    if report is not None:
        report.write_text(
            json.dumps(
                {
                    "status": "boot_error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    raise


if __name__ == "__main__":
    raise SystemExit(main())
