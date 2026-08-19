import os
import re
from datetime import datetime
from pathlib import Path

import pytest

from .adb import Adb, list_devices
from .config import load_settings
from .exceptions import NoDeviceError
from .instrumentation import run_instrumentation
from .logcat import LogcatRecorder

ARTIFACT_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def pytest_addoption(parser):
    group = parser.getgroup("atf", "Android test framework")
    group.addoption("--device", action="append", default=[],
                    help="Device serial to run on. Repeatable. Default: all connected.")
    group.addoption("--atf-config", action="store", default="config/settings.yaml",
                    help="Path to the framework YAML config.")
    group.addoption("--apk", action="store", default=None,
                    help="APK to install before the session (overrides config).")
    group.addoption("--reinstall", action="store_true", default=False,
                    help="Reinstall the app under test at session start.")
    group.addoption("--no-logcat", action="store_true", default=False,
                    help="Disable per-test logcat capture.")
    group.addoption("--no-report", action="store_true", default=False,
                    help="Suppress the end-of-run test report.")
    group.addoption("--report-file", action="store", default="test-report.txt",
                    help="Where to write the test report. Empty string disables the file.")


def pytest_configure(config):
    config.addinivalue_line("markers", "ui: test drives the UI through Appium")
    config.addinivalue_line("markers", "instrumented: test runs an on-device instrumentation suite")
    config.addinivalue_line("markers", "adb: test only needs adb/shell access")
    config.addinivalue_line("markers", "system: read-only assertion about system state")
    config.addinivalue_line("markers", "mutates: changes device state; restored in teardown")
    config.addinivalue_line("markers", "min_sdk(level): skip on devices below this API level")
    _ACTIVE["config"] = config


def _resolve_serials(config):
    """Returns (serials, skip_reason). Cached for the session."""
    cached = getattr(config, "_atf_serials", None)
    if cached is not None:
        return cached

    requested = config.getoption("--device") or (
        [s.strip() for s in os.environ.get("ATF_DEVICES", "").split(",") if s.strip()]
    )
    try:
        online = [d.serial for d in list_devices() if d.state == "device"]
    except NoDeviceError as exc:
        result = ([], str(exc))
    else:
        missing = [s for s in requested if s not in online]
        if requested and missing:
            raise pytest.UsageError(
                f"Requested device(s) not available: {', '.join(missing)}. "
                f"Online: {', '.join(online) or 'none'}"
            )
        if requested:
            result = (requested, None)
        elif online:
            result = (online, None)
        else:
            result = ([], "No Android devices connected. Check `adb devices` and "
                          "that USB debugging is authorized on the device.")

    config._atf_serials = result
    return result


def pytest_generate_tests(metafunc):
    if "device_serial" not in metafunc.fixturenames:
        return
    serials, skip_reason = _resolve_serials(metafunc.config)
    if skip_reason:
        params = [pytest.param(None, marks=pytest.mark.skip(reason=skip_reason),
                               id="no-device")]
        metafunc.parametrize("device_serial", params, scope="session")
    else:
        metafunc.parametrize("device_serial", serials, ids=serials, scope="session")


@pytest.fixture(scope="session")
def settings(pytestconfig):
    cfg = load_settings(pytestconfig.getoption("--atf-config"))
    if pytestconfig.getoption("--apk"):
        cfg.app.apk_path = pytestconfig.getoption("--apk")
    if pytestconfig.getoption("--reinstall"):
        cfg.reinstall_app = True
    if pytestconfig.getoption("--no-logcat"):
        cfg.capture_logcat = False
    cfg.artifacts_dir = Path(cfg.artifacts_dir).absolute()
    cfg.artifacts_dir.mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture(scope="session")
def adb(device_serial, settings):
    device = Adb(device_serial)
    device.wait_for_boot()
    if settings.reinstall_app and settings.app.apk_path:
        device.install(settings.app.apk_path)
    if settings.app.test_apk_path:
        device.install(settings.app.test_apk_path)
    return device


@pytest.fixture(scope="session")
def device_info(adb):
    return {
        "serial": adb.serial,
        "model": adb.model,
        "android_version": adb.android_version,
        "sdk": adb.sdk_version,
    }


@pytest.fixture(scope="session")
def system(adb):
    """Read-only accessor for Android system state."""
    from .system import System
    return System(adb)


@pytest.fixture
def ui(adb, system):
    """Drive and inspect the screen over adb (no Appium). Needs the screen on."""
    from .ui import Ui
    if system.screen_on() is False:
        pytest.skip("screen is off; uiautomator cannot dump the hierarchy")
    return Ui(adb)


@pytest.fixture
def device_state(adb):
    """Mutate system state safely: every change is restored during teardown."""
    from .state import StateGuard
    guard = StateGuard(adb)
    yield guard
    guard.restore()


@pytest.fixture
def artifact_dir(request, settings, device_serial):
    name = ARTIFACT_SAFE.sub("_", request.node.name)[:120]
    path = settings.artifacts_dir / ARTIFACT_SAFE.sub("_", device_serial) / name
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(autouse=True)
def _min_sdk_guard(request):
    marker = request.node.get_closest_marker("min_sdk")
    if marker:
        device = request.getfixturevalue("adb")
        required = marker.args[0]
        if device.sdk_version < required:
            pytest.skip(f"requires API {required}, device is API {device.sdk_version}")


@pytest.fixture(autouse=True)
def logcat(request):
    if "device_serial" not in request.fixturenames:
        yield None
        return
    settings = request.getfixturevalue("settings")
    if not settings.capture_logcat:
        yield None
        return
    artifact_dir = request.getfixturevalue("artifact_dir")
    device = request.getfixturevalue("adb")
    recorder = LogcatRecorder(device, artifact_dir / "logcat.txt").start()
    yield recorder
    recorder.stop()


@pytest.fixture
def app(adb, settings):
    """Fresh app state per test: clear data, then launch."""
    package = settings.app.package
    if not package:
        pytest.skip("no app.package configured")
    if settings.clear_data_between_tests:
        adb.clear_app_data(package)
    adb.launch_app(package, settings.app.activity or None)
    yield adb
    adb.force_stop(package)


@pytest.fixture
def driver(request, settings, device_serial, artifact_dir):
    from .driver import create_driver, server_status

    if server_status(settings.appium.server_url) is None:
        pytest.skip(
            f"Appium server not reachable at {settings.appium.server_url}. "
            "Start it with `appium` (install: npm i -g appium && "
            "appium driver install uiautomator2)."
        )
    session = create_driver(settings, device_serial)
    request.node.stash[_DRIVER_KEY] = session
    yield session
    try:
        if request.node.stash.get(_FAILED_KEY, False) and settings.screenshot_on_failure:
            session.get_screenshot_as_file(str(artifact_dir / "failure.png"))
            (artifact_dir / "page_source.xml").write_text(
                session.page_source, encoding="utf-8"
            )
    finally:
        session.quit()


@pytest.fixture
def instrumentation(adb, settings):
    """Run the configured instrumentation runner and return parsed results."""
    def _run(test_class=None, test_package=None, extra_args=None, timeout=1800):
        runner = settings.app.instrumentation_runner
        if not runner:
            pytest.skip("no app.instrumentation_runner configured")
        return run_instrumentation(
            adb, runner, test_class=test_class, test_package=test_package,
            extra_args=extra_args, timeout=timeout,
        )
    return _run


_FAILED_KEY = pytest.StashKey[bool]()
_DRIVER_KEY = pytest.StashKey[object]()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    report = yield
    result = report.get_result()
    if result.when == "call" and result.failed:
        item.stash[_FAILED_KEY] = True
        _capture_adb_screenshot(item)


def _capture_adb_screenshot(item):
    """Fallback capture for non-Appium tests, using adb screencap."""
    if _DRIVER_KEY in item.stash:
        return
    try:
        device = item.funcargs.get("adb")
        target = item.funcargs.get("artifact_dir")
        if device and target:
            device.screenshot(str(Path(target) / "failure.png"))
    except Exception:
        pass


# --- end-of-run report -------------------------------------------------------

OUTCOME_ORDER = {"failed": 0, "error": 1, "skipped": 2, "passed": 3}
OUTCOME_LABEL = {"passed": "PASS", "failed": "FAIL", "error": "ERROR",
                 "skipped": "SKIP"}

# pytest_runtest_logreport does not receive `config`, so it is captured here
# during configuration.
_ACTIVE = {}


def _record(config, nodeid, outcome, duration, reason=""):
    """Keep the most significant outcome seen for a test across its phases."""
    store = getattr(config, "_atf_results", None)
    if store is None:
        store = config._atf_results = {}
    previous = store.get(nodeid)
    if previous is None or OUTCOME_ORDER[outcome] < OUTCOME_ORDER[previous["outcome"]]:
        store[nodeid] = {"outcome": outcome, "duration": duration, "reason": reason}
    else:
        previous["duration"] += duration


def pytest_runtest_logreport(report):
    config = _ACTIVE.get("config")
    if config is None:
        return
    # Skips are checked first: pytest.skip() inside a test body produces a
    # `call` report that is skipped-but-not-passed, which a when=="call" branch
    # would otherwise record as a failure.
    if report.skipped:
        outcome = "skipped"
    elif report.when == "call":
        outcome = "passed" if report.passed else "failed"
    elif report.failed:
        outcome = "error"          # setup/teardown blew up
    else:
        return                     # uneventful setup/teardown
    reason = ""
    if outcome == "skipped" and isinstance(report.longrepr, tuple):
        reason = report.longrepr[2].replace("Skipped: ", "")
    elif outcome in ("failed", "error"):
        reason = str(report.longrepr).strip().splitlines()[-1] if report.longrepr else ""
    _record(config, report.nodeid, outcome, getattr(report, "duration", 0.0), reason)


MAX_NAME = 72


def _split_nodeid(nodeid):
    """'tests/test_x.py::test_name[serial]' -> ('tests/test_x.py', 'test_name[serial]')

    Parametrised ids can embed whole command dumps, so the bracketed part is
    shortened — the test name itself is never truncated.
    """
    path, _, rest = nodeid.partition("::")
    name = rest.replace("::", " > ")
    if len(name) > MAX_NAME and "[" in name:
        base, _, params = name.partition("[")
        params = params.rstrip("]")
        room = max(8, MAX_NAME - len(base) - 5)
        if len(params) > room:
            params = params[:room] + "..."
        name = f"{base}[{params}]"
    return path, name


def _build_report(config):
    store = getattr(config, "_atf_results", {}) or {}
    suites = {}
    for nodeid, result in store.items():
        path, name = _split_nodeid(nodeid)
        suites.setdefault(path, []).append((name, result))

    lines = []
    totals = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for path in sorted(suites):
        cases = suites[path]
        counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
        for _, result in cases:
            counts[result["outcome"]] += 1
            totals[result["outcome"]] += 1
        elapsed = sum(r["duration"] for _, r in cases)
        summary = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
        lines.append((None, f"{path}  ({summary}, {elapsed:.2f}s)"))
        for name, result in cases:
            label = OUTCOME_LABEL[result["outcome"]]
            line = f"  {label:<5}  {name}  ({result['duration']:.2f}s)"
            if result["reason"] and result["outcome"] != "passed":
                line += f"\n           {result['reason'][:100]}"
            lines.append((result["outcome"], line))
    return lines, totals


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if config.getoption("--no-report"):
        return
    lines, totals = _build_report(config)
    if not lines:
        return

    writer = terminalreporter
    writer.write_sep("=", "test report", bold=True)
    for outcome, text in lines:
        if outcome is None:
            writer.write_line(text, bold=True)
        elif outcome == "passed":
            writer.write_line(text, green=True)
        elif outcome == "skipped":
            writer.write_line(text, yellow=True)
        else:
            writer.write_line(text, red=True)

    tally = "  ".join(f"{n} {k}" for k, n in totals.items() if n) or "no tests run"
    writer.write_line("")
    writer.write_line(f"Total: {tally}", bold=True)

    target = config.getoption("--report-file")
    if target:
        path = Path(config.rootpath) / target
        header = [
            "Test report",
            f"Generated: {datetime.now():%Y-%m-%d %H:%M:%S}",
            f"Result:    {tally}",
            "",
        ]
        body = [text for _, text in lines]
        path.write_text("\n".join(header + body) + "\n", encoding="utf-8")
        writer.write_line(f"Report written to {path}")


def pytest_report_header(config):
    try:
        devices = list_devices()
    except Exception as exc:
        return f"atf: device discovery failed: {exc}"
    if not devices:
        return "atf: no devices connected"
    return "atf devices: " + ", ".join(
        f"{d.serial} ({d.model or d.state})" for d in devices
    )


def pytest_sessionstart(session):
    session.config._atf_started = datetime.now()
