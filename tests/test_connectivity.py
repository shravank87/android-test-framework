"""Radio state and connectivity.

Tests marked `mutates` toggle real radios and restore them in teardown.
Deselect them with `-m "system and not mutates"`.

Assertions here are deliberately structural rather than value-based: the network
name and address are the user's, so tests check that an SSID is present and an
RSSI is plausible, never that they equal a particular value. That keeps the suite
portable across networks and keeps identifying details out of test output.
"""
import time

import pytest

VALID_PRIVATE_DNS = {"off", "opportunistic", "hostname"}


@pytest.fixture
def online(system):
    """Skip a test when the device has no usable network."""
    if system.radios().airplane_mode:
        pytest.skip("airplane mode is on")
    if not system.wifi_is_connected():
        pytest.skip("not associated with a Wi-Fi network")
    return system


# --- radio state ---

@pytest.mark.system
def test_radio_state_is_readable(system):
    radios = system.radios()
    assert radios.airplane_mode is not None
    assert radios.wifi_enabled is not None


@pytest.mark.system
def test_wifi_is_connected(system):
    if not system.radios().wifi_enabled:
        pytest.skip("Wi-Fi is disabled on this device")
    assert system.wifi_is_connected(), "Wi-Fi enabled but not associated with a network"


# --- association quality ---

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


# --- what the platform itself thinks of the network ---

@pytest.mark.system
def test_active_network_is_readable(online):
    assert online.active_network() is not None, (
        "no CONNECTED network found in dumpsys connectivity"
    )


@pytest.mark.system
def test_network_is_validated(online):
    """Android's own connectivity verdict — captive-portal aware, unlike ping."""
    network = online.active_network()
    assert network.validated, (
        f"network is not VALIDATED; capabilities: {sorted(network.capabilities)}"
    )


@pytest.mark.system
def test_network_advertises_internet(online):
    assert online.active_network().has_internet


@pytest.mark.system
def test_network_reports_transport_and_interface(online):
    network = online.active_network()
    assert network.transports, "no transport reported"
    assert network.interface, "no interface name reported"


@pytest.mark.system
def test_network_has_dns_servers(online):
    assert online.active_network().dns, "no DNS servers configured on the network"


@pytest.mark.system
def test_network_has_an_ipv4_address(online):
    assert online.active_network().ipv4_addresses, "no IPv4 address assigned"


@pytest.mark.system
def test_ipv6_is_configured_when_offered(online):
    network = online.active_network()
    if not network.ipv6_addresses:
        pytest.skip("network offers no IPv6")
    assert network.dual_stack, "IPv6 present but no IPv4 address — unexpected on Wi-Fi"


# --- reachability ---

@pytest.mark.system
def test_device_has_internet_access(online):
    assert online.has_internet(), "no ICMP reply from 8.8.8.8"


@pytest.mark.system
def test_dns_resolution_works(online):
    assert online.dns_resolves(), "could not resolve www.google.com"


@pytest.mark.system
def test_private_dns_mode_is_valid(system):
    mode = system.private_dns_mode()
    if mode is None:
        pytest.skip("private DNS mode unset; platform default applies")
    assert mode in VALID_PRIVATE_DNS, f"unrecognised private DNS mode {mode!r}"


# --- cellular ---

@pytest.mark.system
def test_sim_state_is_readable(system):
    assert system.sim_state(), "modem reported no SIM state at all"


@pytest.mark.system
def test_mobile_data_is_available_when_a_sim_is_present(system):
    if not system.has_sim():
        pytest.skip(f"no SIM present (state: {system.sim_state()})")
    assert system.radios().mobile_data is not None


# --- mutating ---

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


@pytest.mark.system
@pytest.mark.mutates
def test_airplane_mode_disables_wifi(system, device_state):
    if system.radios().airplane_mode:
        pytest.skip("airplane mode already on")

    device_state.set_airplane_mode(True)
    assert system.radios().airplane_mode is True

    # Airplane mode should bring the Wi-Fi radio down with it.
    deadline = time.time() + 15
    while time.time() < deadline:
        if "Wifi is disabled" in system.wifi_status():
            break
        time.sleep(1)
    assert "Wifi is disabled" in system.wifi_status()


@pytest.mark.system
@pytest.mark.mutates
def test_network_revalidates_after_airplane_mode(system, device_state):
    """Leaving airplane mode must restore a genuinely working network."""
    if system.radios().airplane_mode or not system.wifi_is_connected():
        pytest.skip("device is not on a validated network to begin with")

    device_state.set_airplane_mode(True)
    device_state.restore()

    deadline = time.time() + 60
    network = None
    while time.time() < deadline:
        network = system.active_network()
        if network is not None and network.validated:
            break
        time.sleep(2)
    assert network is not None and network.validated, (
        "network did not return to VALIDATED within 60s of leaving airplane mode"
    )


@pytest.mark.system
@pytest.mark.mutates
def test_bluetooth_toggle_takes_effect(system, device_state):
    original = system.radios().bluetooth_enabled
    if original is None:
        pytest.skip("device does not report bluetooth_on")

    device_state.set_bluetooth(not original)
    deadline = time.time() + 15
    while time.time() < deadline:
        if system.radios().bluetooth_enabled == (not original):
            break
        time.sleep(1)
    assert system.radios().bluetooth_enabled == (not original)

    device_state.restore()
    time.sleep(3)
    assert system.radios().bluetooth_enabled == original


@pytest.mark.system
@pytest.mark.mutates
def test_state_is_restored_after_mutation(system, adb):
    """The guard itself must leave the device exactly as it found it."""
    from atf import StateGuard

    before = system.radios()
    guard = StateGuard(adb)
    guard.set_wifi(not before.wifi_enabled)
    guard.restore()

    time.sleep(3)
    after = system.radios()
    assert after.wifi_enabled == before.wifi_enabled
