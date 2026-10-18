"""
Access-log hygiene for the readiness poll.

Covers the guarantee that matters operationally: the readiness dashboard's
``since`` cursor stops flooding the access log while everything an operator
needs to read off the line -- method, route, status, client, HTTP version --
survives, and failed requests stay as visible as successful ones.
"""

import logging
import urllib.parse

import pytest

from myra_web.access_log_filter import (
    MIN_ELIDE_LENGTH,
    AccessTargetSanitizer,
    install_access_log_filter,
    sanitize_access_target,
)

READINESS_PATH = "/api/pipeline/readiness"


def _real_fingerprint() -> str:
    """The actual shape the backend sends, so the test can't drift from it."""
    return urllib.parse.quote(
        '{"coverage": 2686, "expected": "2026-10-17", "fundamentals": "2026-09-30",'
        ' "latest": "2026-10-17", "recovery": null, "screening": "lake not built",'
        ' "screening_modes": "degraded", "source": "2026-10-17", "state": "stale",'
        ' "universe": 3465}',
        safe="",
    )


def _access_record(target: str, status: int = 200) -> logging.LogRecord:
    """Build a record shaped exactly like uvicorn's h11 access emission."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1", "GET", target, "1.1", status),
        exc_info=None,
    )


# --------------------------------------------------------------------------- #
# sanitize_access_target
# --------------------------------------------------------------------------- #


def test_readiness_poll_line_shrinks_substantially():
    target = f"{READINESS_PATH}?changed_only=1&since={_real_fingerprint()}"
    assert len(target) > 400, "fixture no longer represents the real payload"

    out = sanitize_access_target(target)

    assert "<elided:" in out
    assert "since=" + _real_fingerprint() not in out
    # The informative parts survive intact.
    assert out.startswith(READINESS_PATH + "?")
    assert "changed_only=1" in out
    # ~466-char line collapses to a readable one.
    assert len(out) < 90


def test_elided_marker_reports_the_cursor_size():
    fingerprint = _real_fingerprint()
    out = sanitize_access_target(f"{READINESS_PATH}?since={fingerprint}")
    assert f"<elided:{len(fingerprint)} chars>" in out


def test_elision_preserves_parameter_order_and_other_params():
    target = f"{READINESS_PATH}?changed_only=1&since={_real_fingerprint()}&days=30"
    out = sanitize_access_target(target)
    keys = [seg.split("=")[0] for seg in out.partition("?")[2].split("&")]
    assert keys == ["changed_only", "since", "days"]


def test_short_since_value_is_left_readable():
    out = sanitize_access_target(f"{READINESS_PATH}?since=abc123")
    assert "since=abc123" in out
    assert "elided" not in out


def test_value_at_threshold_is_not_elided():
    value = "x" * MIN_ELIDE_LENGTH
    out = sanitize_access_target(f"{READINESS_PATH}?since={value}")
    assert value in out


def test_other_paths_keep_their_route_and_method():
    """A large `since` is elided wherever it appears -- it is a cursor param
    everywhere -- but the route that identifies the request survives."""
    target = "/api/scanners/trigger?since=" + _real_fingerprint()
    out = sanitize_access_target(target)
    assert "<elided:" in out
    assert out.startswith("/api/scanners/trigger?")
    assert _real_fingerprint() not in out


def test_other_params_keep_their_exact_encoding():
    """Untouched segments must not be round-tripped through a re-encoder."""
    target = "/api/x?note=a%20b%26c&since=" + _real_fingerprint()
    out = sanitize_access_target(target)
    assert "note=a%20b%26c" in out


def test_target_without_query_is_returned_unchanged():
    target = "/api/pipeline/readiness"
    assert sanitize_access_target(target) is target


def test_empty_query_is_returned_unchanged():
    target = "/api/pipeline/readiness?"
    assert sanitize_access_target(target) is target


def test_unrelated_params_only_target_is_returned_unchanged():
    target = "/api/scanners/trigger?days=30&limit=100"
    assert sanitize_access_target(target) is target


# --------------------------------------------------------------------------- #
# The filter itself
# --------------------------------------------------------------------------- #


def test_filter_rewrites_the_request_target_in_place():
    target = f"{READINESS_PATH}?changed_only=1&since={_real_fingerprint()}"
    record = _access_record(target)

    assert AccessTargetSanitizer().filter(record) is True

    args = record.args
    assert "<elided:" in args[2]
    # Method, client and status are exactly as uvicorn passed them.
    assert args[0] == "127.0.0.1"
    assert args[1] == "GET"
    assert args[3] == "1.1"
    assert args[4] == 200


def test_filter_always_returns_true_so_no_record_is_dropped():
    assert AccessTargetSanitizer().filter(_access_record("/api/health")) is True


def test_failed_requests_remain_fully_observable():
    """A 500 must be as visible as a 200 -- we shorten, never suppress."""
    target = f"{READINESS_PATH}?since={_real_fingerprint()}"
    record = _access_record(target, status=500)

    AccessTargetSanitizer().filter(record)

    assert record.args[4] == 500
    assert record.msg == '%s - "%s %s HTTP/%s" %d'


def test_filter_tolerates_uvicorn_style_dict_args():
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="%(client_addr)s %(request_line)s %(status_code)s",
        args={
            "full_path": f"{READINESS_PATH}?since={_real_fingerprint()}",
            "client_addr": "127.0.0.1",
            "request_line": "GET",
            "status_code": 200,
        },
        exc_info=None,
    )

    AccessTargetSanitizer().filter(record)

    assert "<elided:" in record.args["full_path"]


@pytest.mark.parametrize("args", [(), "not-a-tuple", None, (1, 2)])
def test_filter_ignores_unexpected_shapes(args):
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="something",
        args=args,
        exc_info=None,
    )
    assert AccessTargetSanitizer().filter(record) is True


# --------------------------------------------------------------------------- #
# Installation
# --------------------------------------------------------------------------- #


def test_installation_is_idempotent():
    logger = logging.getLogger("uvicorn.access.test-idempotent")
    logger.filters.clear()

    first = install_access_log_filter(logger=logger)
    second = install_access_log_filter(logger=logger)

    assert first is second
    assert len([f for f in logger.filters if isinstance(f, AccessTargetSanitizer)]) == 1
    logger.filters.clear()


def test_install_does_not_remove_handlers_so_access_logging_stays_on():
    """uvicorn decides to log access lines with ``hasHandlers()``; we must not
    disturb that, or this 'fix' would silently disable the access log."""
    logger = logging.getLogger("uvicorn.access.test-handlers")
    logger.filters.clear()
    logger.addHandler(logging.NullHandler())
    try:
        install_access_log_filter(logger=logger)
        assert logger.hasHandlers(), "handler was lost -- access logging would stop"
    finally:
        logger.filters.clear()
        logger.removeHandler(logger.handlers[-1])
        logger.propagate = True


def test_end_to_end_through_a_real_logger_handler(caplog):
    logger = logging.getLogger("uvicorn.access.test-e2e")
    logger.filters.clear()
    install_access_log_filter(logger=logger)
    try:
        with caplog.at_level(logging.INFO, logger="uvicorn.access.test-e2e"):
            logger.info(
                '%s - "%s %s HTTP/%s" %d',
                "127.0.0.1",
                "GET",
                f"{READINESS_PATH}?changed_only=1&since={_real_fingerprint()}",
                "1.1",
                200,
            )
    finally:
        logger.filters.clear()

    message = caplog.records[-1].getMessage()
    assert "<elided:" in message
    assert _real_fingerprint() not in message
    assert "GET" in message and "200" in message
