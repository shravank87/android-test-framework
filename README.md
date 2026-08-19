# atf — Android System Test Framework

[![CI](https://github.com/shravank87/android-test-framework/actions/workflows/ci.yml/badge.svg)](https://github.com/shravank87/android-test-framework/actions/workflows/ci.yml)

A pytest-based framework for testing the **Android system itself** on physical
devices over adb — platform integrity, security posture, radios, power, thermal,
memory, display, and audio. It is not an app-testing framework; nothing here
requires an APK.

## Test areas

| Suite | Covers |
|---|---|
| [test_platform_security.py](tests/test_platform_security.py) | SELinux enforcing, bootloader lock, verified boot, encryption, debuggable/user build, security-patch age, root check |
| [test_wifi.py](tests/test_wifi.py) | Association quality, signal, band, scanning, saved networks, connecting to the network in test data. Starts from no saved networks |
| [test_wifi_ui.py](tests/test_wifi_ui.py) | The same radio through the Settings UI: picker renders, toggle matches the radio, joining a network by typing its password |
| [test_connectivity.py](tests/test_connectivity.py) | The active network whichever transport carries it: validation, addressing, DNS, reachability, SIM state, airplane/Bluetooth toggles |
| [test_power_thermal.py](tests/test_power_thermal.py) | Battery level/health/temperature/voltage, charging coherence, thermal throttling, memory pressure, load average |
| [test_display_audio.py](tests/test_display_audio.py) | Resolution/density, brightness, screen timeout, rotation, per-stream volumes |
| [test_device_smoke.py](tests/test_device_smoke.py) | Boot state, screenshots, foreground-activity detection |
| [test_ui_toggles.py](tests/test_ui_toggles.py) | System-UI checks over adb: switch state matches the setting it represents |
| [test_system_parsers.py](tests/test_system_parsers.py) | Offline — pins every parser to real device output, no hardware needed |

## Safety model

Tests are split by whether they touch device state:

- **`@pytest.mark.system`** — read-only observation. Safe on a phone you use daily.
- **`@pytest.mark.mutates`** — changes real state (toggles Wi-Fi, airplane mode,
  brightness). Every change goes through the `device_state` fixture, which records
  the prior value on first touch and restores it in teardown — in reverse order,
  even when the test fails.

Run only the safe ones:

```bash
pytest -m "system and not mutates"
```

Mutating tests briefly drop connectivity. They restore it, but don't run them
while relying on the device's network.

## Setup

```bash
pip install -e .
```

Requires Android platform-tools on `PATH` (or `ANDROID_HOME` / `ADB_PATH` set),
USB debugging enabled, and the host authorized on the device. Verify with:

```bash
adb devices
```

No root required — everything uses shell-user permissions.

## Running

Everything, on every connected device (117 tests):

```bash
pytest
```

Read-only checks only — safe on a phone you are using:

```bash
pytest -m "system and not mutates"
```

One suite:

```bash
pytest tests/test_wifi.py
```

One test:

```bash
pytest tests/test_wifi.py::test_wifi_scan_finds_networks
```

Anything matching a keyword, across files:

```bash
pytest -k wifi
```

No hardware needed — the offline parser suites (38 tests):

```bash
pytest tests/test_system_parsers.py tests/test_framework_unit.py
```

Add `-v` for one line per test instead of dots, `-x` to stop at the first
failure, and `-n auto` to run devices in parallel.

### Selecting by marker

| Marker | Tests | Meaning |
|---|---|---|
| `system` | 67 | System-level checks |
| `mutates` | 11 | Changes device state, restored in teardown |
| `adb` | 7 | Plain device/shell automation |

### Choosing devices

Tests taking a device fixture are parametrized across every connected device, so
IDs read `test_selinux_is_enforcing[<serial>]`. Narrow with `--device <serial>`,
repeatable for several. With none connected, device tests skip rather than fail.

### Useful flags

| Flag | Effect |
|---|---|
| `--report-dir DIR` | Where run folders go (default `Results`) |
| `--no-report` | Write no files at all; terminal summary only |
| `--bugreport never` | Skip the end-of-run bugreport |
| `--testdata PATH` | Use a different test data file |
| `--redact-secrets` | Mask passwords in the run log and logcat |
| `--no-logcat` | Skip per-test logcat capture |

## What a run produces

Every run writes a timestamped folder:

```
Results/2026-08-18_21-52-12/
├── report.html                 suites, test names, results, failure reasons
├── report.txt                  the same as plain text
└── artifacts/
    ├── test_run.log            timestamped log of the whole run
    └── <serial>/
        ├── bugreport.zip       only if something failed
        └── <test name>/
            ├── logcat.txt      captured per test
            └── failure.png     only on failure
```

`test_run.log` records every device command and its full output, so a test reads
back as the actions it took and what each returned:

```
21:52:12.611  ── TEST test_connects_to_configured_network
21:52:12.611  connecting to HomeNet (wpa3)
21:52:12.611  shell cmd wifi connect-network HomeNet wpa3 hunter2
21:52:12.725  associated at -61 dBm on 5GHz
21:52:12.799  TEST PASS  test_connects_to_configured_network (0.53s)
```

Commands are logged verbatim, credentials included — pass `--redact-secrets` to
mask them before sharing a run folder. A bugreport is taken once at the end of a
run that had failures, not per failing test: each costs about 70s and 10MB.

`Results/` is gitignored. A second folder, `ClaudeRuns/`, keeps runs performed by
an assistant separate, so `Results/` only holds runs you asked for:

```bash
pytest tests/test_wifi.py --report-dir ClaudeRuns
```

## Test data

Tests needing real details — a network and its password — read them from
`config/testdata.yaml`, which is gitignored. Copy the template and fill it in:

```bash
cp config/testdata.example.yaml config/testdata.yaml
```

```yaml
wifi:
  default:
    ssid: "YOUR_NETWORK"
    password: "YOUR_PASSWORD"
    security: wpa3        # open | wep | wpa2 | wpa3
```

Reach it through the `test_data` fixture; tests skip when the file is absent, so
the suite still runs without one.

```python
def test_connects(test_data, adb):
    network = test_data.wifi_network("default")
```

## Continuous integration

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on every push and pull
request to `main`, across Python 3.9, 3.11 and 3.12.

Hosted runners have no Android device attached, so device-dependent tests skip by
design and the offline parser suite carries the run — currently **38 tests
executed, 79 skipped**. CI deliberately runs the *whole* suite rather than just
the offline files: if a device test ever started erroring instead of skipping
without hardware, that regression would surface here.

A guard step fails the build if fewer than 20 tests actually execute, so a run
cannot go green by silently skipping everything. A second job checks that every
module imports and byte-compiles.

To get real device coverage in CI you would need a self-hosted runner with a
phone attached, or a cloud device farm.

## Fixtures

- `system` — read-only `System` facade: `.platform()`, `.battery()`, `.thermal()`,
  `.memory()`, `.radios()`, `.display()`, `.volume(stream)`, `.wifi_info()`,
  `.wifi_scan()`, `.active_network()`, `.has_internet()`
- `device_state` — mutate with guaranteed restore: `.put_setting()`, `.set_wifi()`,
  `.set_airplane_mode()`, `.set_bluetooth()`
- `ui` — drive the screen over adb, no Appium: `.tap()`, `.scroll_to()`,
  `.set_switch()`, `.type_into()`. Wakes and unlocks the device first
- `test_data` — networks and credentials from `config/testdata.yaml`
- `step` — log a narrative line into `test_run.log`
- `adb` — raw device access for anything the facade doesn't cover
- `device_info` — serial, model, Android version, SDK level
- `artifact_dir` — per-test, per-device output directory
- `logcat` — autouse; writes `logcat.txt` per test

## Writing tests

Read-only assertion:

```python
@pytest.mark.system
def test_battery_is_healthy(system):
    battery = system.battery()
    assert battery.health == "good"
    assert battery.temperature_c < 45.0
```

Mutating, with automatic restore:

```python
@pytest.mark.system
@pytest.mark.mutates
def test_airplane_mode_kills_wifi(system, device_state):
    device_state.set_airplane_mode(True)      # restored in teardown
    assert "Wifi is disabled" in system.wifi_status()
```

Gate on API level with `@pytest.mark.min_sdk(30)`.

## Driving the UI without Appium

[ui.py](atf/ui.py) drives the screen using `uiautomator dump` over plain adb — no
Appium server, no Node, no agent APK. The `ui` fixture provides:

```python
ui.open_settings("android.settings.WIRELESS_SETTINGS")
ui.set_switch("Airplane mode", True)      # tap a row's switch, wait for state
ui.scroll_to(text="About phone")          # swipe until on screen and tappable
ui.type_into("battery", resource_id="search_src_text")
ui.tap(text="Storage")
```

Verified working: finding by text/resource-id/class, reading `checked` state,
tapping, scrolling long lists, text entry, long-press, and back/home navigation.

**Known limits**, each confirmed on-device:

| Limit | Detail |
|---|---|
| Continuously animating screens | `uiautomator dump` waits for the UI to go idle and fails with `could not get idle state` if it never does. Spinners, progress cards and video break it. Appium's driver can set `waitForIdleTimeout` to skip the wait; adb cannot. |
| Unicode text | `input text` mangles accented characters and can throw a NullPointerException. `type_text()` rejects non-ASCII instead of corrupting the field. Use an IME like ADBKeyBoard if you need it. |
| Shell metacharacters | `adb shell` hands the command to the device's shell, so `&`, `(`, `)` truncate input unless quoted device-side. `type_text()` handles the quoting. |
| Repeated keyevents | Passing several keycodes to one `input keyevent` does not repeat the key — on Android 16 it injects literal `GT GT GT` text. Deletes are sent individually. |
| Multi-touch | No pinch, zoom or rotate — `input` is single-pointer only. Appium is required for those. |
| Still frames | A dump is a snapshot, so re-dump after every interaction; waiting is polling, not event-driven. |

Nodes clipped at the fold can report inverted bounds (`[221,2360][482,2337]`), so
`UiNode.usable` gates whether a centre point is safe to tap and `scroll_to` keeps
scrolling until the match is genuinely on screen.

## A note on parsers

Every parser in [system.py](atf/system.py) was written against real output
captured from a device, not from documentation — `dumpsys` field names drift
between Android releases. (Android 16 renamed `mResumedActivity` to
`topResumedActivity`, which silently broke activity detection until it was caught
on hardware.) [test_system_parsers.py](tests/test_system_parsers.py) pins each
parser to verbatim device output and runs without a device, so format regressions
surface immediately.

When adding a reader, probe the real command output first and add a fixture-based
parser test alongside it.

## Configuration

`config/settings.yaml` holds defaults, overridable by environment variable
(`ATF_DEVICES`, `ATF_ARTIFACTS_DIR`, `ATF_MAX_PATCH_AGE_DAYS`) or CLI flag
(`--device`, `--atf-config`, `--no-logcat`).

The `app`/`appium` blocks are only used by the optional UI layer
([test_ui_settings.py](tests/test_ui_settings.py)), which drives the system
Settings app through Appium. Those tests skip cleanly unless an Appium server is
running; the rest of the framework does not depend on them.
