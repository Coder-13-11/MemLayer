"""Vocabulary and validation shared by the store, analyzer, server and CLI."""

from __future__ import annotations

import re
from typing import Any

RECORD_KINDS = ("objective", "finding", "decision", "assumption", "hypothesis", "evidence", "question", "task", "note")
SOURCE_KINDS = ("conversation", "llm_output", "document", "manual_note", "web_page")
VERIFICATION = ("unverified", "evidence_supported", "challenged", "contradicted")
LIFECYCLE = ("active", "superseded", "resolved", "retracted")
RELATION_TYPES = ("supports", "challenges", "contradicts", "depends_on", "requires", "supersedes", "answers", "relates_to")

# The single status a reader sees: lifecycle wins once a record no longer applies.
STATUSES = ("unverified", "evidence_supported", "challenged", "contradicted", "superseded", "resolved", "retracted")

IMPORTANCE = {"low": 1, "normal": 2, "high": 3}
IMPORTANCE_NAMES = {v: k for k, v in IMPORTANCE.items()}

MAX_TITLE = 300
MAX_CONTENT = 200_000
_ID_RE = re.compile(r"^(prj|rec|src|evd|rel)_[0-9a-f]{12}$")


class ValidationError(ValueError):
    """Input that violates the memory model. Raised before anything is written."""


class NotFound(LookupError):
    """A referenced project or record does not exist."""


def effective_status(verification: str, lifecycle: str) -> str:
    return verification if lifecycle == "active" else lifecycle


def require_choice(value: str, choices: tuple[str, ...], field: str) -> str:
    if value not in choices:
        raise ValidationError(f"{field} must be one of {', '.join(choices)}; got {value!r}")
    return value


def require_text(value: Any, field: str, max_len: int = MAX_TITLE, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    value = value.strip()
    if not value and not allow_empty:
        raise ValidationError(f"{field} must not be empty")
    if len(value) > max_len:
        raise ValidationError(f"{field} is longer than {max_len} characters")
    return value


def optional_text(value: Any, field: str, max_len: int = MAX_TITLE) -> str | None:
    if value is None:
        return None
    value = require_text(value, field, max_len, allow_empty=True)
    return value or None


def require_url(value: str | None, field: str = "url") -> str | None:
    if value is None or value == "":
        return None
    value = require_text(value, field, 2000)
    if not re.match(r"^https?://[^\s]+$", value):
        raise ValidationError(f"{field} must be an http(s) URL; got {value!r}")
    return value


def require_id(value: str, field: str = "id") -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValidationError(f"{field} is not a valid memory id: {value!r}")
    return value


def importance_value(value: int | str) -> int:
    if isinstance(value, str):
        if value not in IMPORTANCE:
            raise ValidationError(f"importance must be one of low, normal, high; got {value!r}")
        return IMPORTANCE[value]
    if value not in (1, 2, 3):
        raise ValidationError(f"importance must be 1, 2 or 3; got {value!r}")
    return value


def normalize_key(text: str | None) -> str | None:
    """Normalize a subject/value so '10 m' and '10m ' compare equal."""
    if text is None:
        return None
    return re.sub(r"\s+", "", text.strip().lower()) or None
