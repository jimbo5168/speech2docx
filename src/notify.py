"""macOS user notifications. Failures are ignored so the pipeline can continue."""

from __future__ import annotations

import subprocess


def notify(title: str, body: str) -> None:
    title = title.replace('"', "“")
    body = body.replace('"', "“")
    script = f'display notification "{body}" with title "{title}"'
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script], check=False, capture_output=True, timeout=10)
    except Exception:
        pass
