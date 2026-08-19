import time

import pytest


@pytest.mark.adb
def test_device_is_booted(adb):
    assert adb.shell("getprop", "sys.boot_completed") == "1"


@pytest.mark.adb
def test_device_reports_identity(device_info):
    assert device_info["serial"]
    assert device_info["sdk"] >= 21


@pytest.mark.adb
@pytest.mark.min_sdk(24)
def test_screenshot_capture(adb, artifact_dir):
    path = adb.screenshot(str(artifact_dir / "home.png"))
    assert (artifact_dir / "home.png").stat().st_size > 0
    assert path.endswith("home.png")


@pytest.mark.adb
def test_screen_size_is_sane(adb):
    width, height = adb.screen_size()
    assert width > 0 and height > 0


@pytest.mark.adb
def test_settings_app_is_installed(adb):
    assert adb.is_installed("com.android.settings")


@pytest.mark.adb
def test_launch_reports_foreground_activity(adb):
    adb.launch_app("com.android.settings")
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if adb.current_package() == "com.android.settings":
                break
            time.sleep(0.5)
        assert adb.current_package() == "com.android.settings"
        assert "/" in adb.current_activity()
    finally:
        adb.force_stop("com.android.settings")


@pytest.mark.adb
def test_home_key_returns_to_launcher(adb):
    adb.launch_app("com.android.settings")
    adb.keyevent(3)  # KEYCODE_HOME
    time.sleep(1.5)
    assert adb.current_package() != "com.android.settings"
