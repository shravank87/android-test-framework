"""Wi-Fi: association quality, scanning, and connecting to a known network.

This suite starts from no saved networks — they are cleared once before it runs
— and builds its link from config/testdata.yaml. Nothing here depends on how the
device happened to be configured beforehand.

Assertions are structural rather than value-based: the visible networks belong to
whoever is nearby, so tests check that an SSID is present and an RSSI plausible,
never that either equals a particular value.

Tests marked `mutates` toggle the radio or change saved networks and restore
them in teardown. Deselect with `-m "system and not mutates"`.
"""
import time

import pytest

from .conftest import connect_to


@pytest.fixture(scope="module", autouse=True)
def _clean_slate(wifi_clean_slate):
    """Opt this suite into starting from no saved networks."""
    yield wifi_clean_slate


# --- association ---

@pytest.mark.system
def test_wifi_is_enabled(system):
    enabled = system.radios().wifi_enabled
    assert enabled is not None, "device does not report wifi_on"
    if not enabled:
        pytest.skip("Wi-Fi is disabled on this device")


@pytest.mark.system
def test_wifi_is_connected(online):
    assert online.wifi_is_connected(), (
        "Wi-Fi enabled but not associated with a network"
    )


@pytest.mark.system
def test_wifi_association_is_complete(online):
    info = online.wifi_info()
    assert info is not None, "connected but association details could not be parsed"
    assert info.ssid, "associated network reports no SSID"
    assert info.supplicant_state == "COMPLETED", (
        f"supplicant state is {info.supplicant_state!r}, expected COMPLETED"
    )


@pytest.mark.system
def test_wifi_signal_is_plausible(online):
    rssi = online.wifi_info().rssi
    assert rssi is not None
    assert -100 <= rssi <= 0, f"RSSI {rssi} dBm is outside the physical range"


@pytest.mark.system
def test_wifi_signal_is_usable(online):
    info = online.wifi_info()
    assert info.signal_quality != "poor", (
        f"signal quality is poor ({info.rssi} dBm); connectivity results will be flaky"
    )


@pytest.mark.system
def test_wifi_link_speed_is_positive(online):
    speed = online.wifi_info().link_speed_mbps
    assert speed is not None and speed > 0, f"negotiated link speed is {speed}"


@pytest.mark.system
def test_wifi_operates_on_a_known_band(online):
    info = online.wifi_info()
    assert info.frequency_mhz, "no operating frequency reported"
    assert info.band in ("2.4GHz", "5GHz", "6GHz"), (
        f"frequency {info.frequency_mhz}MHz maps to {info.band}"
    )


# --- scanning ---

@pytest.mark.system
def test_wifi_scan_finds_networks(system, step):
    """Scan for access points and check every row is well formed."""
    if not system.radios().wifi_enabled:
        pytest.skip("Wi-Fi is disabled; scanning is unavailable")

    step("triggering a Wi-Fi scan")
    results = system.wifi_scan()
    assert results, "scan returned no access points at all"
    step(f"scan returned {len(results)} access points")

    for ap in results:
        assert -100 <= ap.rssi <= 0, f"{ap.bssid} reports RSSI {ap.rssi} dBm"
        assert ap.band in ("2.4GHz", "5GHz", "6GHz"), (
            f"{ap.bssid} on {ap.frequency_mhz}MHz maps to band {ap.band}"
        )
        assert ap.age_seconds >= 0, f"{ap.bssid} has negative scan age"

    # A hidden access point still scans, just without a name.
    named = [ap for ap in results if not ap.hidden]
    step(f"{len(named)} named, {len(results) - len(named)} hidden")
    assert named, "every access point reported a blank SSID, which suggests a parse failure"


@pytest.mark.system
def test_scan_sees_the_connected_network(online, step):
    """The network the device is joined to must appear in its own scan."""
    connected = online.wifi_info().ssid
    results = online.wifi_scan()
    step(f"looking for the connected SSID among {len(results)} results")
    assert any(ap.ssid == connected for ap in results), (
        f"the connected network is missing from the scan ({len(results)} seen)"
    )


# --- saved networks ---

@pytest.mark.system
def test_saved_networks_are_readable(system):
    for network in system.saved_networks():
        assert network.network_id >= 0
        assert network.ssid, f"saved network {network.network_id} reports no SSID"


# --- connecting ---

@pytest.mark.system
@pytest.mark.mutates
def test_connects_to_configured_network(system, forgotten_networks, step):
    """Connect to the network named in config/testdata.yaml.

    Saved networks are cleared first, so this is a genuine connection rather
    than an assertion about a link that already existed.
    """
    if not system.radios().wifi_enabled:
        pytest.skip("Wi-Fi is disabled on this device")

    network = forgotten_networks
    step(f"connecting to {network.ssid} ({network.security})")
    assert connect_to(system, network), (
        f"did not associate with {network.ssid} within the timeout"
    )

    info = system.wifi_info()
    assert info is not None and info.ssid == network.ssid
    step(f"associated at {info.rssi} dBm on {info.band}")

    # Associating is not the same as reaching the internet.
    deadline = time.time() + 45
    active = None
    while time.time() < deadline:
        active = system.active_network()
        if active is not None and active.validated:
            break
        time.sleep(2)
    assert active is not None and active.validated, (
        "connected to the network but it never validated"
    )
    step("network validated")


@pytest.mark.system
@pytest.mark.mutates
def test_connecting_saves_the_network(system, forgotten_networks, step):
    """Connecting must leave the network in the saved list for next time."""
    network = forgotten_networks
    assert not system.saved_networks(), "networks were not cleared before the test"

    assert connect_to(system, network), f"did not associate with {network.ssid}"
    saved = system.saved_networks()
    step(f"{len(saved)} network(s) saved after connecting")
    assert any(entry.ssid == network.ssid for entry in saved), (
        f"{network.ssid} was not saved after a successful connection"
    )


@pytest.mark.system
@pytest.mark.mutates
def test_disabling_wifi_takes_effect(system, device_state):
    if not system.radios().wifi_enabled:
        pytest.skip("Wi-Fi already disabled")

    device_state.set_wifi(False)
    assert "Wifi is disabled" in system.wifi_status()

    device_state.set_wifi(True)
    deadline = time.time() + 20
    while time.time() < deadline:
        if "Wifi is enabled" in system.wifi_status():
            break
        time.sleep(1)
    assert "Wifi is enabled" in system.wifi_status()
