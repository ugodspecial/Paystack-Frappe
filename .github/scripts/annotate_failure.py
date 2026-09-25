"""
Publish the end of the CI logs as error annotations.

Job logs are served from blob storage that some tooling cannot reach;
annotations are part of the check run and always readable through the API.
Usage: annotate_failure.py <ci.log> [<bench logs dir>]
"""

import pathlib
import re
import sys

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MAX_ANNOTATIONS = 9
LINES_PER_ANNOTATION = 45


def clean(text: str) -> list:
    lines = []
    for raw in text.splitlines():
        line = ANSI.sub("", raw.split("\r")[-1]).rstrip()
        if line and not line.startswith("Updating DocTypes"):
            lines.append(line)
    return lines


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit(title: str, lines: list) -> None:
    print(f"::error title={escape(title)}::{escape(chr(10).join(lines))}")


chunks = []
ci_log = pathlib.Path(sys.argv[1])
if ci_log.exists():
    lines = clean(ci_log.read_text(errors="replace"))[-(LINES_PER_ANNOTATION * 6):]
    for index in range(0, len(lines), LINES_PER_ANNOTATION):
        chunks.append((f"ci.log part {index // LINES_PER_ANNOTATION + 1}", lines[index:index + LINES_PER_ANNOTATION]))

if len(sys.argv) > 2 and pathlib.Path(sys.argv[2]).is_dir():
    for log in sorted(pathlib.Path(sys.argv[2]).glob("*.log")):
        lines = clean(log.read_text(errors="replace"))[-30:]
        if lines and any(word in "\n".join(lines) for word in ("Error", "Traceback", "Exception")):
            chunks.append((f"bench {log.name}", lines))

for title, lines in chunks[-MAX_ANNOTATIONS:]:
    emit(title, lines)
