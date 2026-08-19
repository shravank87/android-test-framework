"""Fixtures shared by the Wi-Fi and connectivity suites.

Deliberately none of these are autouse: the offline suites must stay runnable
without a device, and an autouse fixture here would drag the device fixtures into
every test in this directory.
"""
import time

import pytest


def connect_to(system, network, timeout=45):
    """Associate with a configured network. Returns True once connected."""
    args = ["cmd", "wifi", "connect-network", network.ssid, network.security]
    if not network.is_open:
        args.append(network.password)
    system.adb.shell(*args, check=False)

    deadline = time.time() + timeout
    while time.time() < deadline:
        info = system.wifi_info()
        if info is not None and info.ssid == network.ssid:
            return True
        time.sleep(2)
    return False


@pytest.fixture(scope="module")
def wifi_clean_slate(system, test_data):
    """Clear saved networks once, then connect from config/testdata.yaml.

    A suite opting into this starts from no stored credentials, so nothing it
    asserts depends on how the device happened to be configured beforehand.

    Without that file the device is left alone: credentials cannot be read back
    off Android, so forgetting them would leave no way to reconnect.
    """
    if not test_data.available:
        yield None
        return

    network = test_data.wifi_network("default")
    if system.wifi_enabled():
        system.forget_all_networks()
        time.sleep(2)
        connect_to(system, network)
    yield network
    # Leave the device connected for whatever runs next.
    if not system.wifi_is_connected():
        connect_to(system, network)


@pytest.fixture
def forgotten_networks(system, test_data, step):
    """Clear saved networks for a single test, then restore afterwards.

    Used by the connect test: without it that test passes trivially, since the
    device is already associated and the first check succeeds whether or not the
    credentials were ever exercised.
    """
    if not test_data.available:
        pytest.skip("no config/testdata.yaml; refusing to forget networks that "
                    "could not then be restored")

    network = test_data.wifi_network("default")
    removed = system.forget_all_networks()
    step(f"forgot {len(removed)} saved network(s) before connecting")
    time.sleep(2)

    yield network

    if not system.wifi_is_connected():
        step(f"restoring {network.ssid}")
        connect_to(system, network)
    step("device restored to its network")


@pytest.fixture
def online(system, test_data):
    """Guarantee a Wi-Fi link, connecting from test data if there is not one.

    Checks are ordered so the common case — already associated — asks the device
    about nothing but Wi-Fi. Airplane mode is only consulted to explain why a
    link is missing, and is read on its own rather than through radios(), which
    would also query Bluetooth and mobile data.
    """
    if system.wifi_is_connected():
        return system

    if not system.wifi_enabled():
        reason = "Wi-Fi is disabled on this device"
        if system.airplane_mode():
            reason = "airplane mode is on, so Wi-Fi is down"
        pytest.skip(reason)
    if not test_data.available:
        pytest.skip("not associated, and no config/testdata.yaml to connect with")

    network = test_data.wifi_network("default")
    assert connect_to(system, network), (
        f"could not connect to {network.ssid} from test data; "
        "check the SSID, security type and passphrase in config/testdata.yaml"
    )
    return system
