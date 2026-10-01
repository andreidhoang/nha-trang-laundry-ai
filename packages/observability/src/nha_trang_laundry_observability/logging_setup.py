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
import logging.handlers
import os
import sys
from typing import TextIO

STRUCTURED_LOGGER_NAME = "nha_trang_laundry.structured"
_MARKER = "nha_trang_laundry.structured.handler"
_FILE_MARKER = "nha_trang_laundry.structured.file"

#: `PLATFORM-RESIDUAL-009B` L4. Where the process also appends its structured lines, when set: a
#: file on a host directory, so the record outlives the container. Docker's json-file log -- the
#: only copy before this -- is deleted with the container, and `docker compose up` recreates the
#: API container on every image update, so each deploy erased the fourteen days the operations
#: check and an investigation rely on. Unset (tests, the demo, the worker): stdout only.
LOG_FILE_VARIABLE = "STRUCTURED_LOG_FILE"
#: Size-rotated, oldest dropped: at most `(LOG_FILE_BACKUP_COUNT + 1) * LOG_FILE_MAX_BYTES` on the
#: host disk (100 MiB), and at least `LOG_FILE_BACKUP_COUNT` full files of history -- the
#: arithmetic for fourteen busy days is in `compose.r1.yaml` and held by
#: `packages/evals/tests/test_ops_hardening_contract.py`.
LOG_FILE_MAX_BYTES = 2 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 49


def configure_structured_logging(
    *, stream: TextIO | None = None, level: str | None = None, log_file: str | None = None
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

    # `LOG_LEVEL` is how an operator asks for less diagnostic noise. It is not how they ask to stop
    # recording what the system did, and these lines are records: CSRF rejections, origin
    # rejections, operations checks. Setting `LOG_LEVEL=WARNING` -- an ordinary thing to do on a
    # busy host -- silently discarded all of them, and `emit` still returned True, because a logger
    # below its level does not raise. So the level floors at INFO: DEBUG makes it louder, nothing
    # makes it quieter than the record stream.
    if logging._nameToLevel[resolved] > logging.INFO:
        resolved = "INFO"

    logger.setLevel(resolved)
    logger.propagate = False
    # `dictConfig` with the default `disable_existing_loggers=True` -- uvicorn's own config, and
    # anything else that runs after this -- sets `disabled` on every logger it did not create. The
    # handler survives, the level survives, and not one line is written.
    logger.disabled = False

    _configure_log_file(
        logger, log_file if log_file is not None else os.environ.get(LOG_FILE_VARIABLE), resolved
    )

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


def _configure_log_file(logger: logging.Logger, path: str | None, level: str) -> None:
    """Attach (or keep) the rotating file handler for `path`; `None` or "" removes it.

    A file that cannot be opened -- a host directory the container cannot write -- is said on
    stderr and the process carries on with stdout: the record is not a reason to refuse the shop
    its console, and the operations check's liveness rule (no API line in fifteen minutes) is what
    reports a file that stopped growing.
    """

    for existing in list(logger.handlers):
        if getattr(existing, "_ntl_file_marker", None) != _FILE_MARKER:
            continue
        if path and getattr(existing, "baseFilename", None) == os.path.abspath(path):
            existing.setLevel(level)
            return
        logger.removeHandler(existing)
        existing.close()
    if not path:
        return
    try:
        handler = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as error:
        print(
            f"structured log file {path} cannot be opened ({error.strerror}); "
            "the structured lines go to stdout only",
            file=sys.stderr,
            flush=True,
        )
        return
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(level)
    handler._ntl_file_marker = _FILE_MARKER  # type: ignore[attr-defined]
    logger.addHandler(handler)


def structured_logging_is_live() -> bool:
    """Whether a structured record would actually reach a handler right now.

    `emit` cannot answer this: it returns True whenever the sink did not raise, and a disabled or
    over-levelled logger does not raise. Callers that need the record stream to exist -- the
    operations checks, whose whole output is these events -- can ask before relying on it.
    """

    logger = logging.getLogger(STRUCTURED_LOGGER_NAME)
    if logger.disabled or not logger.isEnabledFor(logging.INFO):
        return False
    return any(getattr(handler, "_ntl_marker", None) == _MARKER for handler in logger.handlers)
