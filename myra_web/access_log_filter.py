"""
Access-log hygiene for MYRA's HTTP layer.

Why this exists
---------------
The readiness dashboard polls ``GET /api/pipeline/readiness?changed_only=1``
roughly every 30 seconds. ``since`` carries a URL-encoded JSON fingerprint of
the last verdict -- 238 raw characters, 390 once percent-encoded, which makes
each access-log line about 466 characters. A dashboard left open all day
therefore buries real log signal under thousands of lines of identical,
unreadable payload.

What this does *not* do
-----------------------
It never disables access logging, and it never touches the request, the
response, the polling interval, or the endpoint contract. Method, route,
status code, client address and HTTP version all still reach the log, and a
failed request is as visible as a successful one -- only the oversized cursor
value is elided.

Why filtering rather than shrinking the cursor
----------------------------------------------
``data_readiness.fingerprint`` is a cross-system contract: the frontend sends
it back verbatim, the backend compares it for equality, and the transport
tests plus ``tools/verify_readiness_transport.py`` pin that behaviour.
Rewriting its wire format to shrink the URL would trade a cosmetic gain for a
compatibility risk across every one of those call sites. Eliding at the log
boundary keeps the contract untouched.

The value is replaced with ``<elided:N chars>`` rather than removed outright:
an operator can still see that a cursor was sent, how large it was, and that
it varied, which is what a log reader actually needs.
"""

import logging

#: Query parameters whose value is an opaque cursor rather than diagnostic
#: detail. Anything at or below the threshold is short enough to read directly,
#: so it is left alone and only genuine bulk is elided.
ELIDED_PARAMS = frozenset({"since"})

#: Values no longer than this are logged verbatim.
MIN_ELIDE_LENGTH = 64

#: uvicorn logs access lines as::
#:
#:     access_logger.info(
#:         '%s - "%s %s HTTP/%s" %d',
#:         client_addr, method, path_with_query_string, http_version, status,
#:     )
#:
#: so the request target -- path *and* query string -- is the third positional
#: argument. Rewriting that one argument preserves everything else the line
#: reports.
TARGET_ARG_INDEX = 2

#: The logger uvicorn emits access records on. Filtering the *logger* (rather
#: than a handler) means the filter applies no matter which handler uvicorn
#: attaches, and uvicorn's own ``access_logger.hasHandlers()`` probe -- which
#: decides whether access logging happens at all -- is left untouched.
ACCESS_LOGGER_NAME = "uvicorn.access"


def sanitize_access_target(target: str) -> str:
    """Return ``target`` with oversized cursor values replaced by a summary.

    The path, every other query parameter, and the parameter order are
    preserved exactly, so the line still identifies the request precisely.
    A target with nothing worth eliding is returned unchanged (and is the very
    same object), so the common case costs nothing.
    """
    if "?" not in target:
        return target

    path, _, query = target.partition("?")
    if not query:
        return target

    # Splice on "&" and substitute only the oversized value. Re-encoding the
    # whole query through urlencode() would both mangle untouched parameters and
    # turn the marker itself into "%3Celided%3A...", which is exactly the
    # unreadable log line this filter exists to remove.
    rebuilt = []
    changed = False
    for segment in query.split("&"):
        key, equals, value = segment.partition("=")
        if equals and key in ELIDED_PARAMS and len(value) > MIN_ELIDE_LENGTH:
            rebuilt.append(f"{key}=<elided:{len(value)} chars>")
            changed = True
        else:
            rebuilt.append(segment)

    if not changed:
        return target
    return f"{path}?{'&'.join(rebuilt)}"


class AccessTargetSanitizer(logging.Filter):
    """Elide oversized query values on uvicorn access records, in place.

    The record's ``args`` tuple is replaced rather than the rendered message so
    that any handler/formatter -- uvicorn's ``AccessFormatter`` included -- sees
    the sanitized target.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - logging API
        args = record.args
        if isinstance(args, tuple) and len(args) > TARGET_ARG_INDEX:
            target = args[TARGET_ARG_INDEX]
            if isinstance(target, str):
                sanitized = sanitize_access_target(target)
                if sanitized is not target:
                    record.args = (
                        *args[:TARGET_ARG_INDEX],
                        sanitized,
                        *args[TARGET_ARG_INDEX + 1 :],
                    )
        elif isinstance(args, dict) and isinstance(args.get("full_path"), str):
            record.args = {
                **args,
                "full_path": sanitize_access_target(args["full_path"]),
            }
        return True


def install_access_log_filter(
    logger_name: str = ACCESS_LOGGER_NAME, logger: logging.Logger | None = None
) -> AccessTargetSanitizer:
    """Attach the sanitizer to the access logger, idempotently.

    Safe to call from several entrypoints (the ASGI app module and the uvicorn
    launcher): repeated calls return the already-installed filter instead of
    stacking copies that would rewrite the same argument over and over.
    """
    target = logger if logger is not None else logging.getLogger(logger_name)
    for existing in target.filters:
        if isinstance(existing, AccessTargetSanitizer):
            return existing
    sanitizer = AccessTargetSanitizer()
    target.addFilter(sanitizer)
    return sanitizer
