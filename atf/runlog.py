"""A single timestamped log of everything a test run did.

Written to <run>/artifacts/test_run.log. Device commands log themselves, so the
file reads as a narrative of each test: the action taken, then its result.

    19:42:07.118  ── TEST test_wifi_is_connected
    19:42:07.121  shell cmd wifi status
    19:42:07.309  ok in 0.19s | Wifi is enabled
    19:42:07.480  TEST PASS  test_wifi_is_connected (0.36s)
"""
import logging

LOGGER = logging.getLogger("atf.run")
CONTINUATION_INDENT = "    "
REDACTED = "***REDACTED***"

_handler = None
_secrets = set()


def register_secret(value):
    """Mask this value everywhere the log would otherwise print it.

    The log records full commands, so a password passed to `cmd wifi
    connect-network` would be written verbatim without this.
    """
    text = str(value or "")
    if len(text) >= 4:          # too short to mask without mangling real output
        _secrets.add(text)


def clear_secrets():
    _secrets.clear()


def redact(text):
    if not text or not _secrets:
        return text
    result = str(text)
    for secret in _secrets:
        result = result.replace(secret, REDACTED)
    return result


def configure(path):
    """Attach a file handler for this run, replacing any previous one."""
    global _handler
    if _handler is not None:
        LOGGER.removeHandler(_handler)
        _handler.close()

    _handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    _handler.setFormatter(
        logging.Formatter("%(asctime)s.%(msecs)03d  %(message)s", datefmt="%H:%M:%S")
    )
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False       # keep it out of pytest's captured output
    return path


def close():
    global _handler
    if _handler is not None:
        LOGGER.removeHandler(_handler)
        _handler.close()
        _handler = None


def enabled():
    return _handler is not None


def block(text):
    """Format command output for the log in full, never truncated.

    Single-line output stays on the result line. Multi-line output is written
    verbatim beneath it, indented so the timestamped lines remain the structure
    of the file.
    """
    if text is None:
        return ""
    body = redact(str(text)).rstrip("\n")
    if not body.strip():
        return ""
    lines = body.split("\n")
    if len(lines) == 1:
        return lines[0]
    indented = "\n".join(CONTINUATION_INDENT + line for line in lines)
    return "\n" + indented


def banner(message):
    LOGGER.info("%s %s", "─" * 2, message)


def action(message):
    LOGGER.info("%s", redact(message))


def result(message):
    LOGGER.info("%s", redact(message))


def step(message):
    """A narrative step logged from inside a test."""
    LOGGER.info("%s", redact(message))


def note(message):
    LOGGER.info("%s", redact(message))
