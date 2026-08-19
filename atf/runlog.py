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
MAX_RESULT_CHARS = 220

_handler = None


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


def condense(text, limit=MAX_RESULT_CHARS):
    """Collapse a command's output to one readable line."""
    if not text:
        return ""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + f"... (+{len(flat) - limit} chars)"


def banner(message):
    LOGGER.info("%s %s", "─" * 2, message)


def action(message):
    LOGGER.info("%s", message)


def result(message):
    LOGGER.info("%s", message)


def step(message):
    """A narrative step logged from inside a test."""
    LOGGER.info("%s", message)


def note(message):
    LOGGER.info("%s", message)
