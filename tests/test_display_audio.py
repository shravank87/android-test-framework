"""Display, audio, and input state.

Tests marked `mutates` change settings and restore them in teardown.
"""
import time

import pytest

SETTLE_SECONDS = 2


def _set_brightness(system, device_state, target, attempts=3):
    """Write screen_brightness until it holds.

    Even with adaptive brightness off, the platform's brightness service may
    asynchronously write its own sensor-derived value shortly after a mode
    change, clobbering ours. Retrying rather than asserting on the first read
    keeps this from flaking.
    """
    for _ in range(attempts):
        device_state.put_setting("system", "screen_brightness", target)
        deadline = time.time() + 3
        while time.time() < deadline:
            if system.display().brightness == target:
                return True
            time.sleep(0.5)
    return False


@pytest.mark.system
def test_display_geometry_is_sane(system):
    display = system.display()
    assert display.width and display.height
    assert display.width > 0 and display.height > 0
    assert display.density and display.density >= 120


@pytest.mark.system
def test_screen_timeout_is_configured(system):
    timeout = system.display().screen_off_timeout_ms
    assert timeout is not None
    assert timeout > 0, "screen timeout of 0 would blank the display immediately"


@pytest.mark.system
def test_brightness_is_within_range(system):
    display = system.display()
    if display.auto_brightness:
        pytest.skip("adaptive brightness is on; the stored value is not authoritative")
    assert display.brightness is not None
    assert 0 <= display.brightness <= 255


@pytest.mark.system
def test_rotation_is_a_valid_orientation(system):
    rotation = system.display().user_rotation
    if rotation is None:
        pytest.skip("device does not expose user_rotation")
    assert rotation in (0, 1, 2, 3)


@pytest.mark.system
def test_media_volume_is_readable(system):
    volume = system.volume("STREAM_MUSIC")
    assert volume is not None, "STREAM_MUSIC block missing from dumpsys audio"
    assert volume.minimum is not None and volume.maximum is not None
    assert volume.minimum <= volume.current <= volume.maximum


@pytest.mark.system
@pytest.mark.parametrize("stream", ["STREAM_RING", "STREAM_ALARM", "STREAM_NOTIFICATION"])
def test_core_audio_streams_are_present(system, stream):
    volume = system.volume(stream)
    assert volume is not None, f"{stream} missing from dumpsys audio"
    assert volume.maximum and volume.maximum > 0


@pytest.mark.system
@pytest.mark.mutates
def test_brightness_change_takes_effect(system, device_state):
    before = system.display()
    if before.brightness is None:
        pytest.skip("device does not expose screen_brightness")

    # Adaptive brightness rewrites screen_brightness from the light sensor, so it
    # must be off for a written value to stick. Turning it off also makes the
    # system flush the sensor-derived value asynchronously, which races our own
    # write — hence the settle before writing and the poll after.
    if before.auto_brightness:
        device_state.put_setting("system", "screen_brightness_mode", 0)
        time.sleep(SETTLE_SECONDS)

    target = 120 if before.brightness < 100 else 40
    assert _set_brightness(system, device_state, target), (
        f"brightness stayed at {system.display().brightness}, expected {target}"
    )

    device_state.restore()
    after = system.display()
    # With adaptive brightness back on, the sensor owns the value again, so only
    # the mode is meaningfully assertable.
    assert after.auto_brightness == before.auto_brightness
    if not before.auto_brightness:
        assert after.brightness == before.brightness


@pytest.mark.system
@pytest.mark.mutates
def test_screen_timeout_change_is_restored(system, device_state):
    original = system.display().screen_off_timeout_ms
    device_state.put_setting("system", "screen_off_timeout", 60000)
    assert system.display().screen_off_timeout_ms == 60000

    device_state.restore()
    assert system.display().screen_off_timeout_ms == original
