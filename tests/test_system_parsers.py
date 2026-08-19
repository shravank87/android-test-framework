"""Offline tests for system-state parsers. No device required.

Fixtures below are verbatim excerpts captured from a Pixel 6a on Android 16,
so these tests pin the parsers to output the platform actually produces.
"""
import pytest

from atf.system import (
    parse_active_network, parse_battery, parse_meminfo, parse_stream_volume,
    parse_thermal, parse_wifi_info,
)

BATTERY_DUMP = """\
Current Battery Service state:
  AC powered: true
  USB powered: false
  Wireless powered: false
  Max charging current: 3000000
  Charge counter: 812000
  status: 2
  health: 2
  present: true
  level: 19
  scale: 100
  voltage: 3972
  temperature: 357
  technology: Li-ion
"""

THERMAL_DUMP = """\
Thermal Status: 0
	Temperature{mValue=33.931, mType=-1, mName=neutral_therm, mStatus=1}
	Temperature{mValue=39.674004, mType=-1, mName=charger_skin_therm, mStatus=0}
	Temperature{mValue=0.0, mType=4, mName=VIRTUAL-USB-UI, mStatus=0}
	Temperature{mValue=35.245003, mType=-1, mName=skin_therm1, mStatus=0}
	Temperature{mValue=33.931, mType=-1, mName=neutral_therm, mStatus=1}
"""

MEMINFO = """\
MemTotal:        5723624 kB
MemFree:          238788 kB
MemAvailable:    2449532 kB
Buffers:            1960 kB
"""

AUDIO_DUMP = """\
- STREAM_VOICE_CALL:
   Muted: false
   Min: 1
   Max: 5
   streamVolume:4
- STREAM_SYSTEM (aliased to: STREAM_RING):
   Muted: true
   Min: 0
   Max: 7
   streamVolume:5
- STREAM_MUSIC:
   Muted: false
   Muted Internally: false
   Min: 0
   Max: 25
   streamVolume:8
   Current: 2 (speaker): 8, 40000000 (default): 8
- STREAM_ALARM:
   Muted: false
   Min: 1
   Max: 7
   streamVolume:6
"""


def test_battery_parses_real_dump():
    battery = parse_battery(BATTERY_DUMP)
    assert battery.level == 19
    assert battery.scale == 100
    assert battery.percent == 19.0
    assert battery.temperature_c == 35.7   # dumpsys reports tenths of a degree
    assert battery.voltage_mv == 3972
    assert battery.health == "good"
    assert battery.status == "charging"
    assert battery.technology == "Li-ion"


def test_battery_charging_flags():
    battery = parse_battery(BATTERY_DUMP)
    assert battery.ac_powered is True
    assert battery.usb_powered is False
    assert battery.charging is True


def test_battery_tolerates_missing_fields():
    battery = parse_battery("Current Battery Service state:\n  level: 50\n")
    assert battery.level == 50
    assert battery.percent is None      # no scale reported
    assert battery.temperature_c is None
    assert battery.health == "unknown"


def test_thermal_parses_status_and_sensors():
    thermal = parse_thermal(THERMAL_DUMP)
    assert thermal.status_code == 0
    assert thermal.status == "none"
    assert thermal.throttling is False
    names = [t.name for t in thermal.temperatures]
    assert "charger_skin_therm" in names
    assert names.count("neutral_therm") == 1, "duplicate sensors must be collapsed"


def test_thermal_hottest_ignores_zero_readings():
    hottest = parse_thermal(THERMAL_DUMP).hottest()
    assert hottest.name == "charger_skin_therm"
    assert hottest.celsius == pytest.approx(39.674, abs=0.01)


def test_thermal_throttling_detected():
    thermal = parse_thermal("Thermal Status: 3\n")
    assert thermal.throttling is True
    assert thermal.status == "severe"


def test_meminfo_parses_and_computes_ratio():
    memory = parse_meminfo(MEMINFO)
    assert memory.total_kb == 5723624
    assert memory.free_kb == 238788
    assert memory.available_kb == 2449532
    assert 0.42 < memory.available_ratio < 0.43


def test_stream_volume_parses_target_block():
    volume = parse_stream_volume(AUDIO_DUMP, "STREAM_MUSIC")
    assert volume.current == 8
    assert volume.minimum == 0
    assert volume.maximum == 25
    assert volume.muted is False


def test_stream_volume_handles_aliased_header():
    volume = parse_stream_volume(AUDIO_DUMP, "STREAM_SYSTEM")
    assert volume.maximum == 7
    assert volume.muted is True


def test_stream_volume_does_not_bleed_into_next_block():
    """STREAM_VOICE_CALL must not pick up STREAM_SYSTEM's values."""
    volume = parse_stream_volume(AUDIO_DUMP, "STREAM_VOICE_CALL")
    assert volume.maximum == 5
    assert volume.current == 4


def test_stream_volume_returns_none_for_absent_stream():
    assert parse_stream_volume(AUDIO_DUMP, "STREAM_BLUETOOTH_SCO") is None


# --- connectivity ---
#
# Structure is copied from real Pixel output; the network identifiers are
# synthetic (documentation IPv6 range, example SSID) so no real network details
# are committed to the repo.

WIFI_STATUS = """\
Wifi is enabled
Wifi scanning is always available
==== Primary ClientModeManager instance ====
Wifi is connected to "TestNet-5G"
WifiInfo: SSID: "TestNet-5G", BSSID: 02:00:00:00:00:01, MAC: 02:00:00:00:00:02, \
IP: /192.0.2.42, Security type: 4, Supplicant state: COMPLETED, \
Wi-Fi standard: 11ax, RSSI: -56, Link speed: 648Mbps, Tx Link speed: 648Mbps, \
Max Supported Tx Link speed: 2401Mbps, Rx Link speed: 816Mbps, \
Frequency: 5785MHz, Net ID: 0, Metered hint: false, score: 74
successfulTxPackets: 81485
"""

WIFI_DISCONNECTED = "Wifi is enabled\nWifi scanning is always available\n"

CONNECTIVITY_DUMP = """\
NetworkAgentInfo{network{154}  ni{WIFI CONNECTED extra: } \
lp{{InterfaceName: wlan0 LinkAddresses: [ fe80::1/64,2001:db8::2/64,192.0.2.42/24 ] \
DnsAddresses: [ /2001:db8::1,/192.0.2.1 ] Domains: null MTU: 0}} \
nc{[ Transports: WIFI Capabilities: NOT_METERED&INTERNET&TRUSTED&NOT_VPN&VALIDATED&NOT_ROAMING \
LinkUpBandwidth>=12000Kbps]}  factorySerialNumber=7}
"""

METERED_DUMP = """\
NetworkAgentInfo{network{99}  ni{CELLULAR CONNECTED extra: } \
lp{{InterfaceName: rmnet0 LinkAddresses: [ 192.0.2.99/24 ] DnsAddresses: [ /192.0.2.1 ]}} \
nc{[ Transports: CELLULAR Capabilities: INTERNET&TRUSTED&NOT_VPN&NOT_ROAMING]}}
"""


def test_wifi_info_parses_association():
    info = parse_wifi_info(WIFI_STATUS)
    assert info.ssid == "TestNet-5G"
    assert info.rssi == -56
    assert info.link_speed_mbps == 648
    assert info.frequency_mhz == 5785
    assert info.standard == "11ax"
    assert info.supplicant_state == "COMPLETED"


def test_wifi_band_derived_from_frequency():
    assert parse_wifi_info(WIFI_STATUS).band == "5GHz"


@pytest.mark.parametrize("freq,expected", [
    (2437, "2.4GHz"), (5180, "5GHz"), (5785, "5GHz"), (6135, "6GHz"), (1000, "unknown"),
])
def test_band_boundaries(freq, expected):
    from atf.system import WifiInfo
    assert WifiInfo(frequency_mhz=freq).band == expected


@pytest.mark.parametrize("rssi,expected", [
    (-40, "excellent"), (-55, "excellent"), (-60, "good"),
    (-70, "fair"), (-85, "poor"),
])
def test_signal_quality_buckets(rssi, expected):
    from atf.system import WifiInfo
    assert WifiInfo(rssi=rssi).signal_quality == expected


def test_wifi_info_is_none_when_not_associated():
    assert parse_wifi_info(WIFI_DISCONNECTED) is None


def test_active_network_parses_capabilities():
    network = parse_active_network(CONNECTIVITY_DUMP)
    assert network.transports == {"WIFI"}
    assert network.interface == "wlan0"
    assert network.validated is True
    assert network.has_internet is True
    assert network.metered is False


def test_active_network_splits_addresses_by_family():
    network = parse_active_network(CONNECTIVITY_DUMP)
    assert network.ipv4_addresses == ["192.0.2.42"]
    assert len(network.ipv6_addresses) == 2
    assert network.dual_stack is True


def test_active_network_parses_dns():
    assert parse_active_network(CONNECTIVITY_DUMP).dns == ["2001:db8::1", "192.0.2.1"]


def test_metered_network_without_validation():
    network = parse_active_network(METERED_DUMP)
    assert network.metered is True, "absence of NOT_METERED means metered"
    assert network.validated is False
    assert network.dual_stack is False


def test_active_network_returns_none_without_connected_block():
    assert parse_active_network("NetworkAgentInfo{network{1} ni{WIFI DISCONNECTED}}") is None
