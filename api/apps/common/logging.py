"""Format `clé=valeur` : c'est ce que Vector/Loki/Promtail avalent sans regex maison.

On y passe systématiquement par un helper (`log_fields`) pour qu'un champ nouveau ne casse pas un
tableau de bord, et pour que les valeurs non sûrles (e-mail, IP) soient hachées et non imprimées.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

SAFE = ("slug", "qr_id", "user_id", "country", "status", "event", "cache", "ms", "bytes")


def hach(value: Any) -> str:
    """Empreinte courte et non réversible d'une valeur personnelle."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12] if value else "-"


def log_fields(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    parts = [f"event={event}"]
    for key, value in fields.items():
        if key in {"ip", "email", "user_agent", "referer"}:
            value = hach(value)
        parts.append(f"{key}={value!r}" if isinstance(value, str) else f"{key}={value}")
    logger.log(level, " ".join(parts))


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{record.levelname.lower()} logger={record.name}"
        message = record.getMessage()
        if record.exc_info:
            message = f"{message} exc={self.formatException(record.exc_info)!r}"
        return f"{base} {message}"
