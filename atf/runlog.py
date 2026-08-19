"""Two logs per run, written side by side in the run's artifacts folder.

`test_run.log` is the narrative: which test ran, the actions it took, the result
of each, and how it ended. Command output stays on the line when it is short;
bulky output is replaced by a pointer so the file stays readable.

    00:13:41.014  ── TEST test_wifi_connect
    00:13:41.090  shell settings get global wifi_on
    00:13:41.169  ok in 0.08s | 1
    00:13:41.238  selecting HomeNet from the list
    00:13:42.652  shell dumpsys connectivity
    00:13:42.760  ok in 0.11s | 487 lines -> test_run_debug.log
    00:13:53.041  TEST PASS  test_wifi_connect (12.03s)

`test_run_debug.log` holds the same entries with every byte of output kept, for
when a failure needs the full dumpsys.
"""
import logging

LOGGER = logging.getLogger("atf.run")
DEBUG_LOGGER = logging.getLogger("atf.run.debug")

CONTINUATION_INDENT = "    "
REDACTED = "***REDACTED***"
DEBUG_FILENAME = "test_run_debug.log"

# Output up to this many lines stays in the narrative log; longer output is
# summarised there and kept in full in the debug log.
INLINE_LINE_LIMIT = 1
INLINE_CHAR_LIMIT = 200

_handler = None
_debug_handler = None

# Logs are written verbatim by default, credentials included: they are a local
# debugging aid and the run folders are gitignored. Pass --redact-secrets to mask
# registered passwords instead, which is worth doing before sharing a run.
_redaction_enabled = False
_secrets = set()


def set_redaction(enabled):
    global _redaction_enabled
    _redaction_enabled = bool(enabled)


def redaction_enabled():
    return _redaction_enabled


def register_secret(value):
    """Track a value so --redact-secrets can mask it."""
    text = str(value or "")
    if len(text) >= 4:          # too short to mask without mangling real output
        _secrets.add(text)


def clear_secrets():
    _secrets.clear()


def redact(text):
    if not _redaction_enabled or not text or not _secrets:
        return text
    result = str(text)
    for secret in _secrets:
        result = result.replace(secret, REDACTED)
    return result


def _formatter():
    return logging.Formatter("%(asctime)s.%(msecs)03d  %(message)s",
                             datefmt="%H:%M:%S")


def configure(path, debug_path=None):
    """Attach handlers for this run, replacing any from a previous one."""
    global _handler, _debug_handler
    for logger, handler in ((LOGGER, _handler), (DEBUG_LOGGER, _debug_handler)):
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()

    _handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    _handler.setFormatter(_formatter())
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False       # keep it out of pytest's captured output

    debug_path = debug_path or str(path).replace("test_run.log", DEBUG_FILENAME)
    _debug_handler = logging.FileHandler(debug_path, mode="w", encoding="utf-8")
    _debug_handler.setFormatter(_formatter())
    DEBUG_LOGGER.addHandler(_debug_handler)
    DEBUG_LOGGER.setLevel(logging.INFO)
    DEBUG_LOGGER.propagate = False
    return path, debug_path


def close():
    global _handler, _debug_handler
    for logger, handler in ((LOGGER, _handler), (DEBUG_LOGGER, _debug_handler)):
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()
    _handler = _debug_handler = None


def enabled():
    return _handler is not None


def _indent(body):
    return "\n".join(CONTINUATION_INDENT + line for line in body.split("\n"))


def _both(message):
    """An entry short enough to belong in both logs."""
    text = redact(message)
    LOGGER.info("%s", text)
    DEBUG_LOGGER.info("%s", text)


# --- entry types ---

def banner(message):
    _both(f"{'─' * 2} {message}")


def action(message):
    _both(message)


def step(message):
    """A narrative step logged from inside a test."""
    _both(message)


def note(message):
    _both(message)


def result(summary, output=""):
    """A command's outcome: concise in the narrative log, complete in the debug log.

    Short output rides along on the result line. Anything longer is counted and
    left to the debug log, which keeps the narrative readable when a single
    dumpsys can run to hundreds of lines.
    """
    body = redact(str(output or "")).rstrip("\n")
    summary = redact(summary)

    if not body.strip():
        LOGGER.info("%s", summary)
        DEBUG_LOGGER.info("%s", summary)
        return

    lines = body.split("\n")
    if len(lines) <= INLINE_LINE_LIMIT and len(body) <= INLINE_CHAR_LIMIT:
        LOGGER.info("%s | %s", summary, lines[0].strip())
    else:
        LOGGER.info("%s | %d lines -> %s", summary, len(lines), DEBUG_FILENAME)
    DEBUG_LOGGER.info("%s |\n%s", summary, _indent(body))


def block(text):
    """Kept for callers that format their own output; prefer result()."""
    if text is None:
        return ""
    body = redact(str(text)).rstrip("\n")
    if not body.strip():
        return ""
    lines = body.split("\n")
    return lines[0] if len(lines) == 1 else "\n" + _indent(body)
