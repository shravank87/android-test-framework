"""Offline tests for the framework itself. No device required."""
import pytest

from atf.instrumentation import parse_instrument_output

SAMPLE = """\
INSTRUMENTATION_STATUS: class=com.example.LoginTest
INSTRUMENTATION_STATUS: test=validCredentials
INSTRUMENTATION_STATUS_CODE: 1
INSTRUMENTATION_STATUS: class=com.example.LoginTest
INSTRUMENTATION_STATUS: test=validCredentials
INSTRUMENTATION_STATUS_CODE: 0
INSTRUMENTATION_STATUS: class=com.example.LoginTest
INSTRUMENTATION_STATUS: test=emptyPassword
INSTRUMENTATION_STATUS: stream=
junit.framework.AssertionFailedError: expected error banner
\tat com.example.LoginTest.emptyPassword(LoginTest.java:42)
INSTRUMENTATION_STATUS_CODE: -2
INSTRUMENTATION_STATUS: class=com.example.LoginTest
INSTRUMENTATION_STATUS: test=skippedCase
INSTRUMENTATION_STATUS_CODE: -3
INSTRUMENTATION_CODE: -1
"""


def test_parses_pass_fail_and_skip():
    result = parse_instrument_output(SAMPLE)
    statuses = {c.name: c.status for c in result.cases}
    assert statuses == {
        "com.example.LoginTest#validCredentials": "passed",
        "com.example.LoginTest#emptyPassword": "failed",
        "com.example.LoginTest#skippedCase": "ignored",
    }


def test_failure_stream_is_captured():
    result = parse_instrument_output(SAMPLE)
    failure = result.failures[0]
    assert "AssertionFailedError" in failure.stream
    assert "LoginTest.java:42" in failure.stream


def test_assert_all_passed_raises_with_detail():
    result = parse_instrument_output(SAMPLE)
    with pytest.raises(AssertionError, match="emptyPassword"):
        result.assert_all_passed()


def test_assert_all_passed_is_quiet_when_green():
    green = "\n".join(SAMPLE.splitlines()[:6])
    parse_instrument_output(green).assert_all_passed()


# The resumed-activity field name changed across Android versions; all of these
# forms have shipped, so the regex must handle each.
@pytest.mark.parametrize("dump,expected", [
    ("  mResumedActivity: ActivityRecord{a1b2 u0 com.example/.MainActivity t9}",
     "com.example/.MainActivity"),
    ("    topResumedActivity=ActivityRecord{120986217 u0 com.android.settings/.Settings t19}",
     "com.android.settings/.Settings"),
    ("  ResumedActivity: ActivityRecord{120986217 u0 com.android.settings/.Settings t19}",
     "com.android.settings/.Settings"),
])
def test_resumed_activity_regex_handles_all_formats(dump, expected):
    from atf.adb import Adb
    assert Adb._RESUMED_RE.search(dump).group(1) == expected


def test_window_focus_fallback_regex():
    from atf.adb import Adb
    dump = "  mCurrentFocus=Window{ab92641 u0 com.android.settings/com.android.settings.Settings}"
    assert (Adb._FOCUS_RE.search(dump).group(1)
            == "com.android.settings/com.android.settings.Settings")


def test_no_match_returns_none_safely():
    from atf.adb import Adb
    assert Adb._RESUMED_RE.search("nothing useful here") is None
