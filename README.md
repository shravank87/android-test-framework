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
| [test_connectivity.py](tests/test_connectivity.py) | Airplane/Wi-Fi/Bluetooth/mobile-data state, Wi-Fi association, internet reachability, DNS, radio toggles |
| [test_power_thermal.py](tests/test_power_thermal.py) | Battery level/health/temperature/voltage, charging coherence, thermal throttling, memory pressure, load average |
| [test_display_audio.py](tests/test_display_audio.py) | Resolution/density, brightness, screen timeout, rotation, per-stream volumes |
| [test_device_smoke.py](tests/test_device_smoke.py) | Boot state, screenshots, foreground-activity detection |

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

Run the whole system suite on every connected device:

```bash
pytest -m system
```

Tests taking a device fixture are parametrized across all connected devices, so
IDs read `test_selinux_is_enforcing[<serial>]`. Target one device with
`--device <serial>`; parallelize across devices with `-n auto`.

The offline parser tests need no hardware at all:

```bash
pytest tests/test_system_parsers.py tests/test_framework_unit.py
```

Produce a shareable report:

```bash
pytest -m system --html=report.html --self-contained-html
```

## Continuous integration

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on every push and pull
request to `main`, across Python 3.9, 3.11 and 3.12.

Hosted runners have no Android device attached, so device-dependent tests skip by
design and the offline parser suite carries the run — currently **38 tests
executed, 69 skipped**. CI deliberately runs the *whole* suite rather than just
the offline files: if a device test ever started erroring instead of skipping
without hardware, that regression would surface here.

A guard step fails the build if fewer than 20 tests actually execute, so a run
cannot go green by silently skipping everything. A second job checks that every
module imports and byte-compiles.

To get real device coverage in CI you would need a self-hosted runner with a
phone attached, or a cloud device farm.

## Fixtures

- `system` — read-only `System` facade: `.platform()`, `.battery()`, `.thermal()`,
  `.memory()`, `.radios()`, `.display()`, `.volume(stream)`, `.has_internet()`
- `device_state` — mutate with guaranteed restore: `.put_setting()`, `.set_wifi()`,
  `.set_airplane_mode()`, `.set_bluetooth()`
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

## Artifacts

Each test writes to `artifacts/<serial>/<test name>/`: `logcat.txt` always, plus
`failure.png` on failure.

## Configuration

`config/settings.yaml` holds defaults, overridable by environment variable
(`ATF_DEVICES`, `ATF_ARTIFACTS_DIR`, `ATF_MAX_PATCH_AGE_DAYS`) or CLI flag
(`--device`, `--atf-config`, `--no-logcat`).

The `app`/`appium` blocks are only used by the optional UI layer
([test_ui_settings.py](tests/test_ui_settings.py)), which drives the system
Settings app through Appium. Those tests skip cleanly unless an Appium server is
running; the rest of the framework does not depend on them.
