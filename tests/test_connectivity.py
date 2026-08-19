"""Connectivity above the radio: what the platform makes of the active network.

Wi-Fi itself — association quality, scanning, connecting — lives in
[test_wifi.py](test_wifi.py). This suite covers the network the device ends up
with, whichever transport carries it: validation, addressing, DNS, reachability,
cellular state, and the radios' effect on connectivity.

Tests marked `mutates` toggle radios and restore them in teardown. Deselect with
`-m "system and not mutates"`.
"""
import time

import pytest

VALID_PRIVATE_DNS = {"off", "opportunistic", "hostname"}


# --- radio state ---

@pytest.mark.system
def test_radio_state_is_readable(system):
    radios = system.radios()
    assert radios.airplane_mode is not None
    assert radios.wifi_enabled is not None


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
