import os
import re
from datetime import datetime
from html import escape
from pathlib import Path

import pytest

from . import runlog
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
    group.addoption("--bugreport", action="store", default="on-failure",
                    choices=("on-failure", "never"),
                    help="Capture an adb bugreport once at the end of the run if "
                         "anything failed (default), or never. Roughly 70s and 10MB.")
    group.addoption("--report-dir", action="store", default="Results",
                    help="Root directory for timestamped run results. "
                         "Empty string writes no files (terminal summary only).")


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


def run_dir(config):
    """The folder holding everything this run produced, or None if disabled.

    Results/<YYYY-MM-DD_HH-MM-SS>/ — reports and per-test artifacts both live
    here, so a run's logcat and screenshots stay with the report describing them.
    """
    cached = getattr(config, "_atf_run_dir", None)
    if cached is not None:
        return cached or None

    report_root = config.getoption("--report-dir")
    # --no-report means no files at all. Without this it still created a run
    # folder holding only a log, which looks like a run whose report vanished.
    if not report_root or config.getoption("--no-report"):
        config._atf_run_dir = False
        return None

    started = getattr(config, "_atf_started", None) or datetime.now()
    path = Path(config.rootpath) / report_root / started.strftime("%Y-%m-%d_%H-%M-%S")
    path.mkdir(parents=True, exist_ok=True)
    config._atf_run_dir = path
    return path


@pytest.fixture(scope="session")
def settings(pytestconfig):
    cfg = load_settings(pytestconfig.getoption("--atf-config"))
    if pytestconfig.getoption("--apk"):
        cfg.app.apk_path = pytestconfig.getoption("--apk")
    if pytestconfig.getoption("--reinstall"):
        cfg.reinstall_app = True
    if pytestconfig.getoption("--no-logcat"):
        cfg.capture_logcat = False
    # Artifacts belong with the run that produced them; the configured
    # artifacts_dir is only the fallback when file output is switched off.
    run = run_dir(pytestconfig)
    cfg.artifacts_dir = (run / "artifacts") if run else Path(cfg.artifacts_dir).absolute()
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
def step():
    """Log a narrative step from inside a test into test_run.log.

        def test_x(system, step):
            step("reading radio state")
    """
    return runlog.step


@pytest.fixture
def artifact_dir(request, settings, device_serial):
    # The serial already names the parent folder, so drop it from the test name.
    stem = _strip_device(request.node.name, _run_serials(request.config))
    name = ARTIFACT_SAFE.sub("_", stem).strip("_")[:120] or "test"
    path = settings.artifacts_dir / ARTIFACT_SAFE.sub("_", device_serial) / name
    path.mkdir(parents=True, exist_ok=True)
    # Published on the item so failure hooks can find it. A fixture resolved
    # indirectly (the autouse logcat fixture pulls this one in) does not appear
    # in item.funcargs, so the hooks cannot look it up there.
    request.node.stash[_ARTIFACT_DIR_KEY] = path
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
_ARTIFACT_DIR_KEY = pytest.StashKey[object]()


def _artifact_dir_of(item):
    """The failing test's artifact directory, or None if it never made one."""
    return item.stash.get(_ARTIFACT_DIR_KEY, None) or item.funcargs.get("artifact_dir")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    report = yield
    result = report.get_result()
    if result.when == "call" and result.failed:
        item.stash[_FAILED_KEY] = True
        _capture_adb_screenshot(item)


def _capture_bugreport(config, writer, totals):
    """Take one bugreport per device at the end of a run that had failures.

    A bugreport is a whole-device snapshot costing ~70s and ~10MB, so it is
    taken once after the suite finishes rather than per failing test.
    """
    if config.getoption("--bugreport") == "never":
        return
    if not (totals.get("failed") or totals.get("error")):
        return

    serials, _ = getattr(config, "_atf_serials", ([], None))
    run = run_dir(config)
    if not serials or run is None:
        return

    for serial in serials:
        target = run / "artifacts" / ARTIFACT_SAFE.sub("_", serial) / "bugreport.zip"
        target.parent.mkdir(parents=True, exist_ok=True)
        writer.write_line(f"run had failures - capturing bugreport for {serial} "
                          f"(~70s)...", yellow=True)
        runlog.banner(f"CAPTURING BUGREPORT for {serial}")
        try:
            path = Adb(serial).bugreport(target)
            size_mb = Path(path).stat().st_size / (1024 * 1024)
            writer.write_line(f"bugreport saved to {path} ({size_mb:.1f}MB)",
                              yellow=True)
            runlog.note(f"bugreport saved ({size_mb:.1f}MB)")
        except Exception as exc:
            writer.write_line(f"bugreport failed: {exc}", red=True)
            runlog.note(f"bugreport failed: {exc}")


def _capture_adb_screenshot(item):
    """Fallback capture for non-Appium tests, using adb screencap."""
    if _DRIVER_KEY in item.stash:
        return
    try:
        device = item.funcargs.get("adb")
        target = _artifact_dir_of(item)
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
    elif outcome in ("failed", "error") and report.longrepr:
        # Prefer the assertion message ("E   AssertionError: patch is 256 days
        # old") over the trailing file:line, which says nothing useful.
        detail = str(report.longrepr).splitlines()
        errors = [ln[2:].strip() for ln in detail if ln.startswith("E ")]
        reason = errors[0] if errors else detail[-1].strip()
    _record(config, report.nodeid, outcome, getattr(report, "duration", 0.0), reason)

    # Log the verdict once the test body has run (or once setup decided its fate).
    if runlog.enabled() and (report.when == "call" or outcome in ("skipped", "error")):
        _, name = _split_nodeid(report.nodeid, _run_serials(config))
        _log_outcome(report, name, outcome, reason)


MAX_NAME = 72


def _run_serials(config):
    serials = list(getattr(config, "_atf_serials", ([], None))[0])
    return serials + ["no-device"]


def _strip_device(name, serials):
    """Drop the device parameter from a test id.

    Tests are parametrised by device, so every id carries the serial. It is the
    same for the whole run and already shown once in the header, so repeating it
    on every row is noise. Handles ids parametrised by device alone
    (`test_x[SERIAL]`) as well as device plus other params
    (`test_x[SERIAL-STREAM_RING]`).
    """
    for serial in serials:
        if not serial:
            continue
        name = name.replace(f"[{serial}-", "[")
        name = name.replace(f"-{serial}]", "]")
        name = name.replace(f"[{serial}]", "")
    return name


def _split_nodeid(nodeid, serials=()):
    """'tests/test_x.py::test_name[serial]' -> ('tests/test_x.py', 'test_name')

    Parametrised ids can embed whole command dumps, so the bracketed part is
    shortened — the test name itself is never truncated.
    """
    path, _, rest = nodeid.partition("::")
    name = _strip_device(rest.replace("::", " > "), serials)
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
    serials = _run_serials(config)
    suites = {}
    for nodeid, result in store.items():
        path, name = _split_nodeid(nodeid, serials)
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

    run = run_dir(config)
    if run is None:
        return

    started = getattr(config, "_atf_started", None) or datetime.now()
    text_body = [
        "Test report",
        f"Generated: {started:%Y-%m-%d %H:%M:%S}",
        f"Result:    {tally}",
        "",
        *[text for _, text in lines],
    ]
    (run / "report.txt").write_text("\n".join(text_body) + "\n", encoding="utf-8")

    html_path = run / "report.html"
    html_path.write_text(_render_html(config, started, totals), encoding="utf-8")
    writer.write_line(f"Report written to {html_path}")
    if runlog.enabled():
        writer.write_line(f"Run log at {run / 'artifacts' / 'test_run.log'}")

    _capture_bugreport(config, writer, totals)


def _render_html(config, started, totals):
    """A plain HTML page listing each suite, its test cases and their results."""
    store = getattr(config, "_atf_results", {}) or {}
    serials = _run_serials(config)
    suites = {}
    for nodeid, result in store.items():
        path, name = _split_nodeid(nodeid, serials)
        suites.setdefault(path, []).append((name, result))

    total = sum(totals.values())
    duration = sum(r["duration"] for r in store.values())
    devices = ", ".join(getattr(config, "_atf_serials", ([], None))[0]) or "none"
    passed_pct = (100.0 * totals["passed"] / total) if total else 0.0

    rows = []
    for path in sorted(suites):
        cases = suites[path]
        counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
        for _, result in cases:
            counts[result["outcome"]] += 1
        summary = ", ".join(f"{n} {k}" for k, n in counts.items() if n)
        rows.append(
            f'<tr class="suite"><td colspan="3">{escape(path)}'
            f'<span class="meta">{escape(summary)}</span></td></tr>'
        )
        for name, result in cases:
            outcome = result["outcome"]
            reason = result["reason"] if outcome != "passed" else ""
            rows.append(
                f"<tr>"
                f'<td class="name">{escape(name)}</td>'
                f'<td><span class="badge {outcome}">{OUTCOME_LABEL[outcome]}</span></td>'
                f'<td class="reason">{escape(reason)}</td>'
                f"</tr>"
            )

    cards = "".join(
        f'<div class="card {key}"><b>{totals[key]}</b><span>{label}</span></div>'
        for key, label in (("passed", "passed"), ("failed", "failed"),
                           ("error", "errors"), ("skipped", "skipped"))
        if totals[key]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test report {started:%Y-%m-%d %H:%M:%S}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
         margin: 0; padding: 2rem 1.25rem; color: #1b2220; background: #f7f9f8; }}
  main {{ max-width: 70rem; margin: 0 auto; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 .25rem; }}
  .sub {{ color: #5d6b68; font-size: .88rem; margin-bottom: 1.25rem; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: .6rem; margin-bottom: 1.5rem; }}
  .card {{ background: #fff; border: 1px solid #dde5e3; border-radius: 4px;
          padding: .6rem .9rem; min-width: 6rem; }}
  .card b {{ display: block; font-size: 1.5rem; line-height: 1.1; }}
  .card span {{ font-size: .72rem; text-transform: uppercase; letter-spacing: .08em;
               color: #5d6b68; }}
  .card.passed b {{ color: #0f7a63; }} .card.failed b {{ color: #c0392b; }}
  .card.error b {{ color: #c0392b; }} .card.skipped b {{ color: #6e7b78; }}
  .wrap {{ overflow-x: auto; }}
  /* Fixed layout keeps long monospace test names from forcing the table wider
     than the page; the name and detail cells wrap instead. */
  table {{ width: 100%; table-layout: fixed; border-collapse: collapse; background: #fff;
          border: 1px solid #dde5e3; border-radius: 4px; font-size: .88rem; }}
  col.c-name {{ width: 55%; }} col.c-result {{ width: 11%; }} col.c-detail {{ width: 34%; }}
  th, td {{ text-align: left; padding: .5rem .75rem; border-bottom: 1px solid #eef2f1; }}
  th {{ background: #f1f5f4; font-size: .72rem; text-transform: uppercase;
       letter-spacing: .08em; color: #5d6b68; }}
  tr.suite td {{ background: #f1f5f4; font-weight: 600; font-family: ui-monospace, Menlo, monospace; }}
  tr.suite .meta {{ float: right; font-weight: 400; color: #5d6b68; font-family: inherit; }}
  td.name {{ font-family: ui-monospace, Menlo, monospace; overflow-wrap: anywhere; }}
  td.reason {{ color: #5d6b68; overflow-wrap: anywhere; }}
  .badge {{ display: inline-block; padding: .08rem .45rem; border-radius: 3px;
           font-size: .72rem; font-weight: 700; letter-spacing: .04em; }}
  .badge.passed {{ background: #e6f2ee; color: #0f6b57; }}
  .badge.failed, .badge.error {{ background: #fae9e7; color: #a93226; }}
  .badge.skipped {{ background: #eef1f0; color: #5d6b68; }}
</style>
</head>
<body>
<main>
  <h1>Test report</h1>
  <p class="sub">{started:%Y-%m-%d %H:%M:%S} &middot; {total} tests &middot;
     {duration:.2f}s &middot; {passed_pct:.0f}% passed &middot; device(s): {escape(devices)}</p>
  <div class="cards">{cards}</div>
  <div class="wrap">
  <table>
    <colgroup><col class="c-name"><col class="c-result"><col class="c-detail"></colgroup>
    <thead><tr><th>Test case</th><th>Result</th><th>Detail</th></tr></thead>
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>
  </div>
</main>
</body>
</html>
"""


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
    run = run_dir(session.config)
    if run is None:
        return
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    runlog.configure(artifacts / "test_run.log")
    runlog.banner(f"RUN STARTED {session.config._atf_started:%Y-%m-%d %H:%M:%S}")


def pytest_unconfigure(config):
    """Close the run log here, not in sessionfinish.

    pytest_terminal_summary — which writes the reports and takes the end-of-run
    bugreport — is driven from the terminal reporter's own sessionfinish, and
    that runs after this plugin's. Closing the log there left it shut before
    those steps could use it.
    """
    if runlog.enabled():
        runlog.banner(f"RUN FINISHED {datetime.now():%Y-%m-%d %H:%M:%S}")
        runlog.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Bracket each test in the run log so its actions are attributable."""
    if runlog.enabled():
        runlog.banner(f"TEST {_strip_device(item.name, _run_serials(item.config))}")
    yield


def _log_outcome(report, name, outcome, reason):
    """Record a test's verdict in the run log.

    Deliberately not named with a pytest_ prefix: pluggy treats any such
    module-level function as a hook implementation and rejects unknown names.
    """
    label = OUTCOME_LABEL[outcome]
    line = f"TEST {label}  {name} ({report.duration:.2f}s)"
    runlog.LOGGER.info(line if not reason else f"{line} | {runlog.block(reason)}")
