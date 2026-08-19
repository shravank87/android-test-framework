"""Wi-Fi through the Settings UI, driven over adb with uiautomator.

The suite in [test_wifi.py](test_wifi.py) exercises the Wi-Fi service directly
with `cmd wifi`. This one goes through the screens a person would use, so it
catches a UI that misreports state or a connect flow that breaks even while the
underlying service is fine.

No Appium involved: screens are read with `uiautomator dump` and driven with
`input`. Credentials come from config/testdata.yaml.
"""
import time

import pytest

# Settings renders the label with a non-breaking hyphen (U+2011), not an ASCII
# "-", so matching on the plain string finds nothing.
WIFI_LABEL = "Wi‑Fi"
USE_WIFI_ROW = f"Use {WIFI_LABEL}"
PASSWORD_FIELD = "password"
WIFI_SETTINGS = "android.settings.WIFI_SETTINGS"


@pytest.fixture
def wifi_screen(ui, adb):
    """Open the Wi-Fi picker fresh, and close Settings afterwards."""
    adb.force_stop("com.android.settings")
    ui.open_settings(WIFI_SETTINGS)
    time.sleep(3)
    yield ui
    adb.force_stop("com.android.settings")
    ui.home()


@pytest.mark.system
def test_wifi_screen_opens(wifi_screen, step):
    """The picker must render its toggle row and a network list."""
    step("opening the Wi-Fi settings screen")
    assert wifi_screen.is_present(text=USE_WIFI_ROW, timeout=10), (
        f"no {USE_WIFI_ROW!r} row on the Wi-Fi screen"
    )
    assert wifi_screen.is_present(text="Networks", timeout=5), (
        "no network list rendered"
    )


@pytest.mark.system
def test_wifi_toggle_matches_the_radio(wifi_screen, system, step):
    """What the switch shows must agree with the radio it represents."""
    displayed = wifi_screen.switch_state(USE_WIFI_ROW)
    assert displayed is not None, "no switch found on the Wi-Fi row"
    step(f"switch shows {displayed}, radio reports {system.wifi_enabled()}")
    assert displayed == system.wifi_enabled(), (
        f"UI shows {displayed}, wifi_on reports {system.wifi_enabled()}"
    )


@pytest.mark.system
def test_connected_network_is_listed(wifi_screen, online, step):
    """The joined network must appear on screen, not just in the service."""
    ssid = online.wifi_info().ssid
    step(f"looking for the connected network on screen")
    assert wifi_screen.scroll_to(text=ssid) is not None, (
        "the connected network is not shown on the Wi-Fi screen"
    )


@pytest.mark.system
@pytest.mark.mutates
def test_connects_through_the_ui(ui, adb, system, forgotten_networks, step):
    """Join a network the way a person does: tap it, type the password, connect.

    Saved networks are cleared first so Settings actually prompts — otherwise the
    device rejoins on its own and nothing about the dialog is exercised. The SSID
    and password come from config/testdata.yaml.
    """
    network = forgotten_networks

    adb.force_stop("com.android.settings")
    ui.open_settings(WIFI_SETTINGS)
    time.sleep(3)

    step(f"selecting {network.ssid} from the list")
    row = ui.scroll_to(text=network.ssid)
    assert row is not None, f"{network.ssid} is not listed on the Wi-Fi screen"
    ui.tap_node(row)

    step("entering the passphrase")
    field = ui.find(resource_id=PASSWORD_FIELD, timeout=10)
    assert field is not None, (
        "no password field appeared; the device may still have credentials saved"
    )
    ui.tap_node(field)
    ui.type_text(network.password)

    step("tapping Connect")
    assert ui.is_present(text="Connect", timeout=5), "no Connect button in the dialog"
    ui.tap(text="Connect", timeout=5)

    deadline = time.time() + 60
    info = None
    while time.time() < deadline:
        info = system.wifi_info()
        if info is not None and info.ssid == network.ssid:
            break
        time.sleep(2)

    assert info is not None and info.ssid == network.ssid, (
        f"did not join {network.ssid} through the UI within 60s"
    )
    step(f"joined at {info.rssi} dBm on {info.band}")

    assert any(entry.ssid == network.ssid for entry in system.saved_networks()), (
        "connected through the UI but the network was not saved"
    )
    adb.force_stop("com.android.settings")
    ui.home()
