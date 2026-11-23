"""safe.py — error text that may leave the service: no absolute paths (they would reach n8n, report.md and manifest.json)."""
import re

# an absolute path (not part of a URL, a fraction or a relative path) -> its last segment
_ABS_PATH = re.compile(r"(?<![\w./:-])/(?:[^\s/'\"<>|]+/)*([^\s/'\"<>|]*)")


def scrub_paths(text) -> str:
    return _ABS_PATH.sub(lambda m: m.group(1), str(text))
