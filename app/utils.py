from __future__ import annotations
import re
import unicodedata
from urllib.parse import urlparse

def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = re.sub(r"[\W_]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())

def valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
        return parsed.scheme in {"https", "http"} and bool(parsed.netloc)
    except ValueError:
        return False

def safe_name(first_name: str | None, fallback: str = "there") -> str:
    return (first_name or fallback).strip()[:100] or fallback
