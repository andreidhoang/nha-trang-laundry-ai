"""Give the structured logger somewhere to go.

`SafeStructuredLogger` serialises a redacted, schema-checked event and hands it to
`logging.getLogger("nha_trang_laundry.structured").info`. Under uvicorn's default `LOGGING_CONFIG`,
which the API image applies, that logger's effective level is WARNING and the root logger has no
handler at all -- measured:

    logger effective level : WARNING
    isEnabledFor(INFO)     : False
    root handlers          : []

So **every `record()` call in the API was a no-op in the container**, including the browser security
boundary's CSRF and origin rejections. The event schema, the redaction and the correlation IDs were
all correct and all discarded, and nothing anywhere said so: `emit` returns True because the sink
did not raise, and a logger below its level does not raise.

This module is the missing half. It is deliberately small and deliberately not a logging framework:
one handler, one stream, one format, applied once.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TextIO

STRUCTURED_LOGGER_NAME = "nha_trang_laundry.structured"
_MARKER = "nha_trang_laundry.structured.handler"


def configure_structured_logging(
    *, stream: TextIO | None = None, level: str | None = None
) -> logging.Logger:
    """Attach one stdout handler to the structured logger, and return it.

    **stdout, not stderr.** A container's stdout is the log stream every host collector reads
    without being configured to, and these lines are records rather than diagnostics.

    **No formatter beyond the message.** `StructuredEvent.serialize` already produces one JSON
    object per line with its own timestamp, severity and correlation id. Wrapping that in a second
    timestamp and level would produce lines that are neither plain text nor valid JSON, which is
    the shape that defeats every parser downstream.

    **`propagate = False`.** Otherwise a root handler installed by anything else -- uvicorn, a test
    harness, a future library -- duplicates every line, and a duplicated audit-adjacent record is
    worse than a missing one because it inflates counts nobody is counting by hand.

    Idempotent: calling it twice does not double the output, because a process that configures
    logging in both an application factory and a startup hook is a normal accident.
    """

    logger = logging.getLogger(STRUCTURED_LOGGER_NAME)
    resolved = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    if resolved not in logging._nameToLevel:
        resolved = "INFO"
    logger.setLevel(resolved)
    logger.propagate = False

    target = stream or sys.stdout
    for existing in list(logger.handlers):
        if getattr(existing, "_ntl_marker", None) != _MARKER:
            continue
        if getattr(existing, "stream", None) is target:
            existing.setLevel(resolved)
            return logger
        # A caller that names a different stream means it. Returning early here -- which is what
        # the first version did -- made the second call a silent no-op, so a test that passed its
        # own buffer got an empty buffer and a process that redirected its output kept writing to
        # the old one. Idempotent must mean "no duplicates", not "the first caller wins".
        logger.removeHandler(existing)
        existing.close()

    handler = logging.StreamHandler(target)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(resolved)
    handler._ntl_marker = _MARKER  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return logger
