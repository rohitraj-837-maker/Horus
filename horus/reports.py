"""Write generated tables into marked sections of files under reports/.

A section looks like:
    <!-- BEGIN:name --> ... <!-- END:name -->
Scripts replace only their own section, so hand-written notes around it survive.
"""

from __future__ import annotations

from pathlib import Path

from horus.config import resolve

REPORTS_DIR = "reports"


def replace_section(filename: str, name: str, body: str, root: str | Path | None = None) -> Path:
    path = (Path(root) if root else resolve(REPORTS_DIR)) / filename
    begin, end = f"<!-- BEGIN:{name} -->", f"<!-- END:{name} -->"
    block = f"{begin}\n{body.rstrip()}\n{end}"
    text = path.read_text() if path.exists() else ""
    if begin in text and end in text:
        pre, rest = text.split(begin, 1)
        _, post = rest.split(end, 1)
        text = pre + block + post
    else:
        text = text.rstrip() + ("\n\n" if text else "") + block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path
