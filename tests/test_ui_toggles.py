"""System-UI testing over plain adb — no Appium server involved.

These verify the Settings UI against the underlying system state: that a switch
displays what the system actually reports, and that tapping it changes the system
rather than just the pixels.
"""
import time

import pytest

WIRELESS = "android.settings.WIRELESS_SETTINGS"
DISPLAY = "android.settings.DISPLAY_SETTINGS"


@pytest.mark.system
def test_settings_screen_dumps(ui):
    ui.open_settings(WIRELESS)
    titles = ui.dump().texts()
    assert "Airplane mode" in titles, f"unexpected screen contents: {titles[:10]}"


@pytest.mark.system
def test_airplane_switch_reflects_system_state(ui, system):
    """The displayed toggle must agree with the setting it represents."""
    ui.open_settings(WIRELESS)
    displayed = ui.switch_state("Airplane mode")
    assert displayed is not None, "no switch found on the Airplane mode row"
    assert displayed == system.radios().airplane_mode, (
        f"UI shows {displayed}, system reports {system.radios().airplane_mode}"
    )


@pytest.mark.system
def test_switch_row_is_located_by_label(ui):
    ui.open_settings(WIRELESS)
    switch = ui.row_switch("Airplane mode")
    assert switch is not None
    assert switch.checkable
    assert "Switch" in switch.class_name
    assert switch.center is not None


@pytest.mark.system
@pytest.mark.mutates
def test_ui_toggle_changes_system_state(ui, system, adb):
    """Tapping the switch must move the real system setting, not just the UI."""
    ui.open_settings(WIRELESS)
    original = system.radios().airplane_mode

    try:
        ui.set_switch("Airplane mode", not original)
        deadline = time.time() + 10
        while time.time() < deadline:
            if system.radios().airplane_mode == (not original):
                break
            time.sleep(0.5)
        assert system.radios().airplane_mode == (not original), (
            "UI switch moved but the system setting did not follow"
        )
        assert ui.switch_state("Airplane mode") == (not original)
    finally:
        # Restore through adb, which works even if the UI is left in a bad state.
        adb.shell("cmd", "connectivity", "airplane-mode",
                  "enable" if original else "disable", check=False)
        time.sleep(3)
        adb.force_stop("com.android.settings")

    assert system.radios().airplane_mode == original


@pytest.mark.system
def test_display_settings_screen_is_reachable(ui):
    ui.open_settings(DISPLAY)
    titles = ui.dump().texts()
    assert any("rightness" in t for t in titles), (
        f"no brightness entry on the display screen: {titles[:10]}"
    )
