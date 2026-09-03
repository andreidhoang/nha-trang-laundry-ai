"""`SHOP-OBSERVABILITY-001`: a structured event has to actually reach a stream.

This is the one test in the repository whose absence was itself the defect. `SafeStructuredLogger`
was correct, its redaction was correct, its schema was correct, and under the configuration the
container actually runs every call was discarded -- because a logger below its level does not
raise, so `emit` returned True and nothing anywhere said otherwise.
"""

from __future__ import annotations

import io
import json
import logging
import logging.config

import pytest
from nha_trang_laundry_observability import (
    STRUCTURED_LOGGER_NAME,
    CorrelationContext,
    SafeStructuredLogger,
    StructuredEvent,
    configure_structured_logging,
)
from uvicorn.config import LOGGING_CONFIG


@pytest.fixture(autouse=True)
def _restore_logging() -> object:
    logger = logging.getLogger(STRUCTURED_LOGGER_NAME)
    handlers, level, propagate = list(logger.handlers), logger.level, logger.propagate
    yield
    logger.handlers = handlers
    logger.setLevel(level)
    logger.propagate = propagate


def test_without_configuration_under_uvicorn_the_event_is_discarded() -> None:
    """The measured defect, pinned so a regression is a failure rather than a silence."""

    logging.config.dictConfig(LOGGING_CONFIG)
    logger = logging.getLogger(STRUCTURED_LOGGER_NAME)
    logger.handlers = []
    logger.propagate = True
    logger.setLevel(logging.NOTSET)

    # The level is the whole defect: INFO is below WARNING, so the record is dropped before any
    # handler is consulted. (In the container the root logger also has no handler at all, but
    # pytest installs one, so that half is measured rather than asserted here.)
    assert logger.getEffectiveLevel() == logging.WARNING
    assert logger.isEnabledFor(logging.INFO) is False
    # And the emitter reports success regardless, which is why nothing caught this.
    assert SafeStructuredLogger().emit(_event()) is True


def test_a_configured_event_reaches_the_stream_as_one_json_object() -> None:
    logging.config.dictConfig(LOGGING_CONFIG)
    stream = io.StringIO()
    configure_structured_logging(stream=stream, level="INFO")

    assert SafeStructuredLogger().emit(_event()) is True

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1, lines
    # One JSON object per line, with no second timestamp or level wrapped around it: a formatter
    # that prefixed anything would produce output that is neither plain text nor parseable JSON.
    document = json.loads(lines[0])
    assert document["event"] == "probe"
    assert document["component"] == "api"


def test_configuring_twice_does_not_duplicate_the_line() -> None:
    """A process that configures logging in a factory and again in a startup hook is normal.

    A duplicated record is worse than a missing one here: these lines are the closest thing the
    deployment has to an operational record, and duplicates inflate counts nobody verifies by hand.
    """

    stream = io.StringIO()
    configure_structured_logging(stream=stream)
    configure_structured_logging(stream=stream)

    SafeStructuredLogger().emit(_event())

    assert len([line for line in stream.getvalue().splitlines() if line.strip()]) == 1


def test_the_structured_logger_does_not_propagate_to_the_root() -> None:
    stream = io.StringIO()
    configure_structured_logging(stream=stream)
    assert logging.getLogger(STRUCTURED_LOGGER_NAME).propagate is False


def test_an_unknown_log_level_falls_back_rather_than_silencing_everything() -> None:
    stream = io.StringIO()
    configure_structured_logging(stream=stream, level="LOUD")

    SafeStructuredLogger().emit(_event())

    assert stream.getvalue().strip(), "a typo in LOG_LEVEL must not silence the deployment"


def _event() -> StructuredEvent:
    return StructuredEvent(
        component="api",
        name="probe",
        outcome="success",
        correlation=CorrelationContext.new(),
        fields={},
    )
