"""Mutating device state with guaranteed restoration.

Every mutation goes through StateGuard, which records the prior value the first
time a key is touched and restores it during teardown — in reverse order, so
dependent changes unwind correctly. Restoration runs even when the test fails.
"""
import time

RADIO_SETTLE_SECONDS = 2


class RestoreError(Exception):
    """Raised when one or more values could not be restored."""


class StateGuard:
    """Records original values and restores them on close."""

    def __init__(self, adb, settle=RADIO_SETTLE_SECONDS):
        self.adb = adb
        self.settle = settle
        self._undo = []

    def _remember(self, description, restore_fn):
        self._undo.append((description, restore_fn))

    # --- settings ---

    def put_setting(self, namespace, key, value):
        original = self.adb.shell("settings", "get", namespace, key, check=False)
        self.adb.shell("settings", "put", namespace, key, str(value))

        def restore():
            if original in ("null", ""):
                self.adb.shell("settings", "delete", namespace, key, check=False)
            else:
                self.adb.shell("settings", "put", namespace, key, original)

        self._remember(f"{namespace}/{key} -> {original}", restore)
        return original

    # --- radios ---

    def set_wifi(self, enabled):
        was_on = self._wifi_enabled()
        self.adb.shell("cmd", "wifi", "set-wifi-enabled",
                       "enabled" if enabled else "disabled")
        time.sleep(self.settle)
        if was_on != enabled:
            self._remember(
                f"wifi -> {'on' if was_on else 'off'}",
                lambda: (self.adb.shell("cmd", "wifi", "set-wifi-enabled",
                                        "enabled" if was_on else "disabled"),
                         time.sleep(self.settle)),
            )
        return was_on

    def _wifi_enabled(self):
        return "Wifi is enabled" in self.adb.shell("cmd", "wifi", "status", check=False)

    def set_airplane_mode(self, enabled):
        was_on = self._airplane_enabled()
        self._apply_airplane(enabled)
        if was_on != enabled:
            self._remember(
                f"airplane -> {'on' if was_on else 'off'}",
                lambda: self._apply_airplane(was_on),
            )
        return was_on

    def _airplane_enabled(self):
        out = self.adb.shell("cmd", "connectivity", "airplane-mode", check=False)
        if out in ("enabled", "disabled"):
            return out == "enabled"
        return self.adb.shell("settings", "get", "global",
                              "airplane_mode_on", check=False) == "1"

    def _apply_airplane(self, enabled):
        self.adb.shell("cmd", "connectivity", "airplane-mode",
                       "enable" if enabled else "disable", check=False)
        time.sleep(self.settle)

    def set_bluetooth(self, enabled):
        was_on = self.adb.shell("settings", "get", "global",
                                "bluetooth_on", check=False) == "1"
        self._apply_bluetooth(enabled)
        if was_on != enabled:
            self._remember(
                f"bluetooth -> {'on' if was_on else 'off'}",
                lambda: self._apply_bluetooth(was_on),
            )
        return was_on

    def _apply_bluetooth(self, enabled):
        self.adb.shell("cmd", "bluetooth_manager",
                       "enable" if enabled else "disable", check=False)
        time.sleep(self.settle)

    # --- teardown ---

    def restore(self):
        failures = []
        for description, restore_fn in reversed(self._undo):
            try:
                restore_fn()
            except Exception as exc:
                failures.append(f"{description}: {exc}")
        self._undo.clear()
        if failures:
            raise RestoreError(
                "failed to restore device state:\n  " + "\n  ".join(failures)
            )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.restore()
