from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_DIR = Path(__file__).resolve().parent
BUDAPEST_TZ = ZoneInfo("Europe/Budapest")


def main() -> int:
    report_date = (datetime.now(BUDAPEST_TZ).date() - timedelta(days=1)).isoformat()
    command = [
        sys.executable,
        str(PROJECT_DIR / "main.py"),
        "--report-type",
        "daily",
        "--start-date",
        report_date,
        "--end-date",
        report_date,
    ]
    return subprocess.call(command, cwd=str(PROJECT_DIR))


if __name__ == "__main__":
    raise SystemExit(main())
