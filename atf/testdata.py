"""Test data loaded from a local, uncommitted file.

Tests that need real-world details — a Wi-Fi network and its password, an
account, a server address — read them from config/testdata.yaml rather than
hard-coding them. That file is gitignored; config/testdata.example.yaml is the
committed template describing its shape.

Every password loaded is registered with the run log so it is masked wherever it
would otherwise be written out, including in the adb command that uses it.
"""
from dataclasses import dataclass, field
from pathlib import Path

from . import runlog

try:
    import yaml
except ImportError:
    yaml = None

DEFAULT_PATH = Path("config/testdata.yaml")
EXAMPLE_PATH = Path("config/testdata.example.yaml")


class MissingTestData(Exception):
    """Raised when a test asks for data the local file does not define."""


@dataclass
class WifiNetwork:
    name: str
    ssid: str
    password: str = ""
    security: str = "wpa2"
    hidden: bool = False

    @property
    def is_open(self):
        return self.security.lower() in ("none", "open", "")

    def __repr__(self):
        # Never let a password reach a traceback or an assertion message.
        return (f"WifiNetwork(name={self.name!r}, ssid={self.ssid!r}, "
                f"security={self.security!r}, password={'set' if self.password else 'unset'})")


@dataclass
class TestData:
    path: Path = None
    wifi: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    @property
    def available(self):
        return bool(self.raw)

    def wifi_network(self, name="default"):
        """Look up a configured network by its key in the file."""
        network = self.wifi.get(name)
        if network is None:
            known = ", ".join(sorted(self.wifi)) or "none"
            raise MissingTestData(
                f"no Wi-Fi network named {name!r} in {self.path or EXAMPLE_PATH}. "
                f"Defined networks: {known}. See {EXAMPLE_PATH} for the format."
            )
        return network

    def get(self, *keys, default=None):
        """Read an arbitrary nested value, e.g. data.get('api', 'base_url')."""
        node = self.raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def load(path=None):
    """Load test data, returning an empty set if the file is absent."""
    path = Path(path or DEFAULT_PATH)
    if not path.exists():
        return TestData(path=path)
    if yaml is None:
        raise RuntimeError("PyYAML is required to read test data")

    raw = yaml.safe_load(path.read_text()) or {}
    networks = {}
    for name, entry in (raw.get("wifi") or {}).items():
        entry = entry or {}
        network = WifiNetwork(
            name=name,
            ssid=entry.get("ssid", ""),
            password=entry.get("password", "") or "",
            security=entry.get("security", "wpa2"),
            hidden=bool(entry.get("hidden", False)),
        )
        runlog.register_secret(network.password)
        networks[name] = network

    for secret in (raw.get("secrets") or {}).values():
        runlog.register_secret(secret)

    return TestData(path=path, wifi=networks, raw=raw)
