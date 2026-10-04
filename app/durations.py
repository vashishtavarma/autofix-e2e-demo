"""Parse human durations like '1h30m' or '45s' into seconds."""
import re

UNITS = {"h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> int:
    parts = re.findall(r"(\d+)([hms])", text.strip().lower())
    if not parts:
        raise ValueError(f"not a duration: {text!r}")
    return sum(int(n) * UNITS[u] for n, u in parts)
