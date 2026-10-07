"""Shared schema helpers and input sanitisation."""
from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import datetime
from typing import Annotated, Generic, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

T = TypeVar("T")

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML_TAG = re.compile(r"<\s*/?\s*[a-zA-Z!][^>]*>")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-.]{0,49}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-.]{1,99}$")
USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]{2,63}$")


def clean_text(value: str) -> str:
    """Normalise unicode and strip control characters (keeps \\t, \\n, \\r)."""
    value = unicodedata.normalize("NFC", value)
    return _CONTROL_CHARS.sub("", value).strip()


def _plain_line(value: str) -> str:
    value = clean_text(value)
    if "\n" in value or "\r" in value:
        raise ValueError("must be a single line")
    if _HTML_TAG.search(value):
        raise ValueError("must not contain HTML markup")
    return value


def _name(value: str) -> str:
    value = _plain_line(value)
    if not NAME_RE.match(value):
        raise ValueError("may only contain letters, digits, spaces, '-', '_' and '.' (2-100 chars)")
    return value


PlainLine = Annotated[str, AfterValidator(_plain_line)]
CleanText = Annotated[str, AfterValidator(clean_text)]
EntityName = Annotated[str, AfterValidator(_name)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class Ref(ORMModel):
    id: uuid.UUID
    name: str


class Message(BaseModel):
    message: str


class Timestamped(ORMModel):
    created_at: datetime
    updated_at: datetime | None = None


def page_params(page: int = 1, page_size: int = 25) -> tuple[int, int]:
    return max(page, 1), min(max(page_size, 1), 200)


PageNumber = Annotated[int, Field(ge=1, le=100000)]
PageSize = Annotated[int, Field(ge=1, le=200)]
