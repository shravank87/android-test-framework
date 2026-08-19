"""Typed readers for Android system state.

Every parser here was written against real `dumpsys`/`settings` output rather
than documentation — field names drift between Android releases, so each parser
tolerates missing keys and returns None instead of raising.
"""
import re
import time
from dataclasses import dataclass, field

# dumpsys battery emits "  key: value"; keys contain spaces ("AC powered").
_KV_RE = re.compile(r"^\s*([A-Za-z][A-Za-z ]*?):\s*(.+?)\s*$")
_TEMP_RE = re.compile(
    r"Temperature\{mValue=([-\d.]+), mType=(-?\d+), mName=([^,]+), mStatus=(\d+)\}"
)
_THERMAL_STATUS_RE = re.compile(r"Thermal Status:\s*(\d+)")
_MEM_RE = re.compile(r"^(\w+):\s+(\d+)\s*kB", re.MULTILINE)
_SIZE_RE = re.compile(r"Physical size:\s*(\d+)x(\d+)")
_OVERRIDE_SIZE_RE = re.compile(r"Override size:\s*(\d+)x(\d+)")
_DENSITY_RE = re.compile(r"Physical density:\s*(\d+)")

BATTERY_HEALTH = {
    1: "unknown", 2: "good", 3: "overheat", 4: "dead",
    5: "over_voltage", 6: "unspecified_failure", 7: "cold",
}
BATTERY_STATUS = {
    1: "unknown", 2: "charging", 3: "discharging", 4: "not_charging", 5: "full",
}
THERMAL_STATUS = {
    0: "none", 1: "light", 2: "moderate", 3: "severe",
    4: "critical", 5: "emergency", 6: "shutdown",
}


def _to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_kv_block(text):
    out = {}
    for line in text.splitlines():
        match = _KV_RE.match(line)
        if match:
            out[match.group(1).strip()] = match.group(2).strip()
    return out


@dataclass
class BatteryState:
    level: int = None
    scale: int = None
    temperature_c: float = None
    voltage_mv: int = None
    health: str = ""
    status: str = ""
    technology: str = ""
    ac_powered: bool = False
    usb_powered: bool = False
    wireless_powered: bool = False

    @property
    def percent(self):
        if self.level is None or not self.scale:
            return None
        return 100.0 * self.level / self.scale

    @property
    def charging(self):
        return self.ac_powered or self.usb_powered or self.wireless_powered


def parse_battery(text):
    kv = parse_kv_block(text)
    raw_temp = _to_int(kv.get("temperature"))
    return BatteryState(
        level=_to_int(kv.get("level")),
        scale=_to_int(kv.get("scale")),
        # dumpsys reports tenths of a degree Celsius.
        temperature_c=raw_temp / 10.0 if raw_temp is not None else None,
        voltage_mv=_to_int(kv.get("voltage")),
        health=BATTERY_HEALTH.get(_to_int(kv.get("health")), "unknown"),
        status=BATTERY_STATUS.get(_to_int(kv.get("status")), "unknown"),
        technology=kv.get("technology", ""),
        ac_powered=kv.get("AC powered") == "true",
        usb_powered=kv.get("USB powered") == "true",
        wireless_powered=kv.get("Wireless powered") == "true",
    )


SKIN_SENSOR_TYPE = 3   # android.os.Temperature.TYPE_SKIN


@dataclass
class Temperature:
    name: str
    celsius: float
    type: int
    status: int

    @property
    def is_skin(self):
        """True only for the platform's declared skin sensor (TYPE_SKIN).

        Deliberately type-based, not name-based. Vendor boards expose raw
        sensors such as `charger_skin_therm` and `skin_therm1` that carry
        "skin" in the name but sit next to the charging circuitry and run far
        hotter than anything the user touches; Android synthesises the real
        figure as a TYPE_SKIN sensor (`VIRTUAL-SKIN`) from them. Matching on
        the name mistakes board sensors for touch temperature.
        """
        return self.type == SKIN_SENSOR_TYPE


@dataclass
class ThermalState:
    status_code: int = 0
    temperatures: list = field(default_factory=list)

    @property
    def status(self):
        return THERMAL_STATUS.get(self.status_code, f"unknown({self.status_code})")

    @property
    def throttling(self):
        return self.status_code > 0

    def hottest(self):
        real = [t for t in self.temperatures if t.celsius > 0]
        return max(real, key=lambda t: t.celsius) if real else None


def parse_thermal(text):
    status_match = _THERMAL_STATUS_RE.search(text)
    temps = [
        Temperature(name=m.group(3), celsius=float(m.group(1)),
                    type=int(m.group(2)), status=int(m.group(4)))
        for m in _TEMP_RE.finditer(text)
    ]
    # The dump repeats sensors across sections; keep the first reading per name.
    seen, unique = set(), []
    for temp in temps:
        if temp.name not in seen:
            seen.add(temp.name)
            unique.append(temp)
    return ThermalState(
        status_code=int(status_match.group(1)) if status_match else 0,
        temperatures=unique,
    )


@dataclass
class MemoryState:
    total_kb: int = None
    free_kb: int = None
    available_kb: int = None

    @property
    def available_ratio(self):
        if not self.total_kb or self.available_kb is None:
            return None
        return self.available_kb / self.total_kb


def parse_meminfo(text):
    values = {k: int(v) for k, v in _MEM_RE.findall(text)}
    return MemoryState(
        total_kb=values.get("MemTotal"),
        free_kb=values.get("MemFree"),
        available_kb=values.get("MemAvailable"),
    )


@dataclass
class StreamVolume:
    name: str
    current: int = None
    minimum: int = None
    maximum: int = None
    muted: bool = False


def parse_stream_volume(text, stream):
    """Parse one `- STREAM_X:` block out of `dumpsys audio`.

    Headers may carry an alias suffix, e.g. `- STREAM_SYSTEM (aliased to: ...)`.
    """
    pattern = re.compile(
        rf"^- {re.escape(stream)}(?: \(aliased to: [^)]+\))?:\s*$", re.MULTILINE
    )
    match = pattern.search(text)
    if not match:
        return None
    rest = text[match.end():]
    next_block = re.search(r"^- STREAM_", rest, re.MULTILINE)
    block = rest[: next_block.start()] if next_block else rest

    def grab(expr, cast=int):
        found = re.search(expr, block)
        return cast(found.group(1)) if found else None

    return StreamVolume(
        name=stream,
        current=grab(r"streamVolume:(\d+)"),
        minimum=grab(r"Min:\s*(\d+)"),
        maximum=grab(r"Max:\s*(\d+)"),
        muted=grab(r"Muted:\s*(\w+)", cast=lambda v: v == "true") or False,
    )


@dataclass
class DisplayState:
    width: int = None
    height: int = None
    density: int = None
    brightness: int = None
    auto_brightness: bool = False
    screen_off_timeout_ms: int = None
    user_rotation: int = None
    auto_rotate: bool = False


@dataclass
class PlatformState:
    sdk: int = None
    release: str = ""
    security_patch: str = ""
    build_type: str = ""
    abi: str = ""
    selinux: str = ""
    verified_boot: str = ""
    bootloader_locked: bool = None
    encryption: str = ""
    debuggable: bool = None
    secure: bool = None


@dataclass
class WifiInfo:
    ssid: str = ""
    rssi: int = None
    link_speed_mbps: int = None
    frequency_mhz: int = None
    standard: str = ""
    supplicant_state: str = ""

    @property
    def band(self):
        """2.4 / 5 / 6 GHz, derived from the operating frequency."""
        f = self.frequency_mhz
        if f is None:
            return None
        if 2400 <= f <= 2500:
            return "2.4GHz"
        if 4900 <= f <= 5900:
            return "5GHz"
        if 5925 <= f <= 7125:
            return "6GHz"
        return "unknown"

    @property
    def signal_quality(self):
        """Coarse bucket for RSSI in dBm."""
        if self.rssi is None:
            return None
        if self.rssi >= -55:
            return "excellent"
        if self.rssi >= -67:
            return "good"
        if self.rssi >= -80:
            return "fair"
        return "poor"


def parse_wifi_info(text):
    """Pull structured fields out of `cmd wifi status`.

    Returns None when Wi-Fi is not associated.
    """
    if "Wifi is connected to" not in text:
        return None

    def grab(pattern, cast=str):
        match = re.search(pattern, text)
        if not match:
            return None
        try:
            return cast(match.group(1))
        except (TypeError, ValueError):
            return None

    return WifiInfo(
        ssid=grab(r'SSID:\s*"([^"]*)"') or "",
        rssi=grab(r"RSSI:\s*(-?\d+)", int),
        link_speed_mbps=grab(r"Link speed:\s*(\d+)Mbps", int),
        frequency_mhz=grab(r"Frequency:\s*(\d+)MHz", int),
        standard=grab(r"Wi-Fi standard:\s*(\S+?),") or "",
        supplicant_state=grab(r"Supplicant state:\s*(\w+)") or "",
    )


@dataclass
class SavedNetwork:
    network_id: int
    ssid: str
    security: str = ""


# "0            MyNetwork                        wpa3-sae" — the SSID may contain
# spaces, so it runs up to the column gap before the security type.
_SAVED_ROW_RE = re.compile(r"^\s*(\d+)\s+(.+?)\s{2,}(\S+)\s*$")


def parse_saved_networks(text):
    """Parse `cmd wifi list-networks`, collapsing duplicate ids.

    A network in WPA2/WPA3 transition mode is listed once per security type
    under the same id; forgetting it once is enough.
    """
    seen, networks = set(), []
    for line in text.splitlines():
        if "Network Id" in line:
            continue           # header
        match = _SAVED_ROW_RE.match(line)
        if not match:
            continue
        network_id = int(match.group(1))
        if network_id in seen:
            continue
        seen.add(network_id)
        networks.append(
            SavedNetwork(network_id=network_id,
                         ssid=match.group(2).strip(),
                         security=match.group(3).strip().rstrip("^"))
        )
    return networks


@dataclass
class ScanResult:
    bssid: str
    frequency_mhz: int
    rssi: int
    age_seconds: float
    ssid: str = ""
    flags: str = ""

    @property
    def hidden(self):
        """Access points that withhold their SSID scan as a blank name."""
        return not self.ssid

    @property
    def band(self):
        return WifiInfo(frequency_mhz=self.frequency_mhz).band

    @property
    def secured(self):
        return any(tag in self.flags for tag in ("WPA", "RSN", "SAE", "WEP"))


# "  <bssid>  <freq>  <rssi>  <age>  <ssid>  <flags>" — the SSID may be empty and
# may itself contain spaces, so it is taken as whatever sits between the age and
# the flags rather than as a whitespace-delimited column.
_SCAN_ROW_RE = re.compile(
    r"^\s*([0-9a-f]{2}(?::[0-9a-f]{2}){5})\s+(\d+)\s+(-?\d+)\s+([\d.]+)\s*(.*?)\s*(\[.*)?$",
    re.IGNORECASE,
)


def parse_scan_results(text):
    """Parse `cmd wifi list-scan-results` into ScanResult rows."""
    results = []
    for line in text.splitlines():
        match = _SCAN_ROW_RE.match(line)
        if not match:
            continue           # header, blank line, or an error message
        results.append(
            ScanResult(
                bssid=match.group(1).lower(),
                frequency_mhz=int(match.group(2)),
                rssi=int(match.group(3)),
                age_seconds=float(match.group(4)),
                ssid=match.group(5) or "",
                flags=match.group(6) or "",
            )
        )
    return results


@dataclass
class NetworkInfo:
    transports: set = field(default_factory=set)
    capabilities: set = field(default_factory=set)
    interface: str = ""
    addresses: list = field(default_factory=list)
    dns: list = field(default_factory=list)

    @property
    def validated(self):
        """Android's own verdict that the network really reaches the internet."""
        return "VALIDATED" in self.capabilities

    @property
    def metered(self):
        return "NOT_METERED" not in self.capabilities

    @property
    def has_internet(self):
        return "INTERNET" in self.capabilities

    @property
    def ipv4_addresses(self):
        return [a for a in self.addresses if ":" not in a]

    @property
    def ipv6_addresses(self):
        return [a for a in self.addresses if ":" in a]

    @property
    def dual_stack(self):
        return bool(self.ipv4_addresses) and bool(self.ipv6_addresses)


def parse_active_network(text):
    """Parse the CONNECTED NetworkAgentInfo block from `dumpsys connectivity`."""
    block = None
    for chunk in text.split("NetworkAgentInfo{")[1:]:
        head = chunk[:4000]
        # Word-boundary match: a plain substring test also matches DISCONNECTED.
        if re.search(r"\bCONNECTED\b", head):
            block = head
            break
    if block is None:
        return None

    def grab(pattern):
        match = re.search(pattern, block)
        return match.group(1) if match else ""

    transports = set(filter(None, grab(r"Transports:\s*([A-Z_&,]+)").split("&")))
    capabilities = set(filter(None, grab(r"Capabilities:\s*([A-Z_&]+)").split("&")))
    addresses = [
        a.split("/")[0].strip()
        for a in grab(r"LinkAddresses:\s*\[([^\]]*)\]").split(",")
        if a.strip()
    ]
    dns = [
        d.strip().lstrip("/")
        for d in grab(r"DnsAddresses:\s*\[([^\]]*)\]").split(",")
        if d.strip()
    ]
    return NetworkInfo(
        transports=transports,
        capabilities=capabilities,
        interface=grab(r"InterfaceName:\s*(\w+)"),
        addresses=addresses,
        dns=dns,
    )


@dataclass
class RadioState:
    airplane_mode: bool = None
    wifi_enabled: bool = None
    bluetooth_enabled: bool = None
    mobile_data: bool = None


class System:
    """System-level facade over an Adb connection. Read-only."""

    def __init__(self, adb):
        self.adb = adb

    # --- settings namespace access ---

    def setting(self, namespace, key):
        value = self.adb.shell("settings", "get", namespace, key, check=False)
        return None if value in ("null", "") else value

    def setting_int(self, namespace, key):
        return _to_int(self.setting(namespace, key))

    def setting_bool(self, namespace, key):
        value = self.setting_int(namespace, key)
        return None if value is None else bool(value)

    # --- platform / security ---

    def platform(self):
        prop = self.adb.getprop
        locked = prop("ro.boot.flash.locked")
        debuggable = prop("ro.debuggable")
        secure = prop("ro.secure")
        return PlatformState(
            sdk=self.adb.sdk_version,
            release=self.adb.android_version,
            security_patch=prop("ro.build.version.security_patch"),
            build_type=prop("ro.build.type"),
            abi=prop("ro.product.cpu.abi"),
            selinux=self.adb.shell("getenforce", check=False),
            verified_boot=prop("ro.boot.verifiedbootstate"),
            bootloader_locked=(locked == "1") if locked else None,
            encryption=prop("ro.crypto.state"),
            debuggable=(debuggable == "1") if debuggable else None,
            secure=(secure == "1") if secure else None,
        )

    def uptime_seconds(self):
        raw = self.adb.shell("cat", "/proc/uptime", check=False)
        return float(raw.split()[0]) if raw else None

    def load_average(self):
        raw = self.adb.shell("cat", "/proc/loadavg", check=False)
        parts = raw.split()
        return tuple(float(p) for p in parts[:3]) if len(parts) >= 3 else None

    # --- power / thermal / memory ---

    def battery(self):
        return parse_battery(self.adb.shell("dumpsys", "battery"))

    def thermal(self):
        return parse_thermal(self.adb.shell("dumpsys", "thermalservice", check=False))

    def memory(self):
        return parse_meminfo(self.adb.shell("cat", "/proc/meminfo"))

    # --- radios / connectivity ---

    def radios(self):
        return RadioState(
            airplane_mode=self.setting_bool("global", "airplane_mode_on"),
            wifi_enabled=self.setting_bool("global", "wifi_on"),
            bluetooth_enabled=self.setting_bool("global", "bluetooth_on"),
            mobile_data=self.setting_bool("global", "mobile_data"),
        )

    def wifi_status(self):
        return self.adb.shell("cmd", "wifi", "status", check=False)

    def wifi_is_connected(self):
        return "Wifi is connected to" in self.wifi_status()

    def wifi_info(self):
        """Structured Wi-Fi association details, or None when not associated."""
        return parse_wifi_info(self.wifi_status())

    def saved_networks(self):
        """Networks the device has stored credentials for."""
        return parse_saved_networks(
            self.adb.shell("cmd", "wifi", "list-networks", check=False)
        )

    def forget_network(self, network_id):
        return self.adb.shell("cmd", "wifi", "forget-network", str(network_id),
                              check=False)

    def forget_all_networks(self):
        """Clear every saved network. Returns the SSIDs removed.

        Credentials cannot be read back off the device, so callers are
        responsible for restoring anything they still need.
        """
        removed = []
        for network in self.saved_networks():
            self.forget_network(network.network_id)
            removed.append(network.ssid)
        return removed

    def wifi_scan(self, trigger=True, settle=4.0):
        """Return the latest Wi-Fi scan results.

        Android throttles scan requests, so a refused `start-scan` is not an
        error — the cached results from the platform's own periodic scan are
        returned instead.
        """
        if trigger:
            self.adb.shell("cmd", "wifi", "start-scan", check=False)
            time.sleep(settle)
        return parse_scan_results(
            self.adb.shell("cmd", "wifi", "list-scan-results", check=False)
        )

    def active_network(self):
        """The connected network's transports, capabilities, addresses and DNS."""
        return parse_active_network(
            self.adb.shell("dumpsys", "connectivity", check=False)
        )

    def sim_state(self):
        """ABSENT, READY, PIN_REQUIRED, ... as reported by the modem."""
        return self.adb.getprop("gsm.sim.state") or "UNKNOWN"

    def has_sim(self):
        return self.sim_state().upper().startswith("READY")

    def private_dns_mode(self):
        """off / opportunistic / hostname; None when unset (platform default)."""
        return self.setting("global", "private_dns_mode")

    def has_internet(self, host="8.8.8.8", timeout=5):
        proc = self.adb.run(
            "shell", "ping", "-c", "1", "-W", str(timeout), host, check=False
        )
        return proc.returncode == 0 and " 0% packet loss" in proc.stdout

    def dns_resolves(self, host="www.google.com", timeout=5):
        proc = self.adb.run(
            "shell", "ping", "-c", "1", "-W", str(timeout), host, check=False
        )
        return proc.returncode == 0

    # --- display / audio ---

    def display(self):
        size_out = self.adb.shell("wm", "size")
        density_out = self.adb.shell("wm", "density")
        # An override (e.g. from `wm size`) takes precedence over the panel size.
        size = _OVERRIDE_SIZE_RE.search(size_out) or _SIZE_RE.search(size_out)
        density = _DENSITY_RE.search(density_out)
        return DisplayState(
            width=int(size.group(1)) if size else None,
            height=int(size.group(2)) if size else None,
            density=int(density.group(1)) if density else None,
            brightness=self.setting_int("system", "screen_brightness"),
            auto_brightness=self.setting_int("system", "screen_brightness_mode") == 1,
            screen_off_timeout_ms=self.setting_int("system", "screen_off_timeout"),
            user_rotation=self.setting_int("system", "user_rotation"),
            auto_rotate=self.setting_bool("system", "accelerometer_rotation"),
        )

    def volume(self, stream="STREAM_MUSIC"):
        return parse_stream_volume(self.adb.shell("dumpsys", "audio"), stream)

    def screen_on(self):
        out = self.adb.shell("dumpsys", "power", check=False)
        match = re.search(r"mWakefulness=(\w+)", out)
        return match.group(1) == "Awake" if match else None
