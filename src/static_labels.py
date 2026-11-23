#!/usr/bin/env python3
# Copyright 2026 Erik Lönroth
# See LICENSE file for licensing details.
"""Parser for the `static-labels` config option."""

from __future__ import annotations

import re

MAX_STATIC_LABELS = 16
MAX_LABEL_LENGTH = 128
_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED_NAMES = frozenset({"job", "instance"})


class StaticLabelsError(ValueError):
    """Raised when the `static-labels` option is malformed."""


def _check_entry(name: str, value: str, entry: str) -> None:
    prefix = f"static-labels entry '{entry}'"
    if not _NAME_PATTERN.match(name):
        raise StaticLabelsError(f"{prefix}: name must match [A-Za-z_][A-Za-z0-9_]*")
    if name.startswith(("juju_", "__")) or name in _RESERVED_NAMES:
        raise StaticLabelsError(f"{prefix}: name is reserved")
    if not value:
        raise StaticLabelsError(f"{prefix}: value must not be empty")
    if len(name) > MAX_LABEL_LENGTH or len(value) > MAX_LABEL_LENGTH:
        raise StaticLabelsError(
            f"{prefix}: name and value must be at most {MAX_LABEL_LENGTH} characters"
        )


def parse_static_labels(raw: str) -> dict[str, str]:
    """Parse comma-separated `name=value` pairs, keeping the order written."""
    labels: dict[str, str] = {}
    for segment in raw.split(","):
        entry = segment.strip()
        if not entry:
            continue
        name, separator, value = entry.partition("=")
        if not separator:
            raise StaticLabelsError(f"static-labels entry '{entry}': expected name=value")
        name, value = name.strip(), value.strip()
        _check_entry(name, value, entry)
        if name in labels:
            raise StaticLabelsError(
                f"static-labels entry '{entry}': label '{name}' appears more than once"
            )
        labels[name] = value
    if len(labels) > MAX_STATIC_LABELS:
        raise StaticLabelsError(
            f"static-labels allows at most {MAX_STATIC_LABELS} labels, got {len(labels)}"
        )
    return labels
