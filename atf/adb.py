import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import runlog
from .exceptions import AdbError, AdbTimeout, NoDeviceError

DEFAULT_TIMEOUT = 60


def adb_binary():
    explicit = os.environ.get("ADB_PATH")
    if explicit:
        return explicit
    found = shutil.which("adb")
    if found:
        return found
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if sdk:
        candidate = os.path.join(sdk, "platform-tools", "adb")
        if os.path.exists(candidate):
            return candidate
    raise NoDeviceError(
        "adb not found. Install Android platform-tools and set ANDROID_HOME, "
        "or point ADB_PATH at the adb binary."
    )


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    state: str
    model: str = ""
    product: str = ""


def _run(args, timeout):
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise AdbTimeout(f"timed out after {timeout}s: {' '.join(args)}") from exc
    return proc


def list_devices():
    proc = _run([adb_binary(), "devices", "-l"], DEFAULT_TIMEOUT)
    if proc.returncode != 0:
        raise AdbError(["adb", "devices", "-l"], proc.returncode, proc.stdout, proc.stderr)
    devices = []
    for line in proc.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        serial, state = parts[0], parts[1]
        attrs = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
        devices.append(
            DeviceInfo(
                serial=serial,
                state=state,
                model=attrs.get("model", ""),
                product=attrs.get("product", ""),
            )
        )
    return devices


class Adb:
    """adb wrapper bound to a single device serial."""

    def __init__(self, serial, timeout=DEFAULT_TIMEOUT):
        self.serial = serial
        self.timeout = timeout
        self._binary = adb_binary()

    def __repr__(self):
        return f"Adb(serial={self.serial!r})"

    def run(self, *args, timeout=None, check=True):
        cmd = [self._binary, "-s", self.serial, *(str(a) for a in args)]
        printable = " ".join(str(a) for a in args)
        if runlog.enabled():
            runlog.action(printable)
        started = time.time()
        try:
            proc = _run(cmd, timeout or self.timeout)
        except AdbTimeout as exc:
            if runlog.enabled():
                runlog.result(f"TIMEOUT after {time.time() - started:.2f}s")
            raise exc
        elapsed = time.time() - started
        if runlog.enabled():
            if proc.returncode == 0:
                runlog.result(f"ok in {elapsed:.2f}s", proc.stdout)
            else:
                runlog.result(f"exit {proc.returncode} in {elapsed:.2f}s",
                              proc.stderr or proc.stdout)
        if check and proc.returncode != 0:
            raise AdbError(cmd, proc.returncode, proc.stdout, proc.stderr)
        return proc

    def shell(self, *args, timeout=None, check=True):
        """Run a shell command. Pass argv as separate args, never a joined string."""
        return self.run("shell", *args, timeout=timeout, check=check).stdout.strip()

    # --- device properties ---

    def getprop(self, name):
        return self.shell("getprop", name)

    @property
    def sdk_version(self):
        return int(self.getprop("ro.build.version.sdk"))

    @property
    def android_version(self):
        return self.getprop("ro.build.version.release")

    @property
    def model(self):
        return self.getprop("ro.product.model")

    def wait_for_device(self, timeout=120):
        self.run("wait-for-device", timeout=timeout)

    def wait_for_boot(self, timeout=180):
        self.wait_for_device(timeout=timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.shell("getprop", "sys.boot_completed", check=False) == "1":
                return
            time.sleep(2)
        raise AdbTimeout(f"{self.serial} did not finish booting within {timeout}s")

    # --- packages ---

    def install(self, apk_path, reinstall=True, grant_permissions=True, allow_downgrade=False):
        args = ["install"]
        if reinstall:
            args.append("-r")
        if grant_permissions:
            args.append("-g")
        if allow_downgrade:
            args.append("-d")
        args.append(apk_path)
        out = self.run(*args, timeout=300).stdout
        if "Success" not in out:
            raise AdbError([*args], 0, out, "")
        return out

    def uninstall(self, package, check=True):
        return self.run("uninstall", package, check=check).stdout

    def is_installed(self, package):
        out = self.shell("pm", "list", "packages", package, check=False)
        return any(line.strip() == f"package:{package}" for line in out.splitlines())

    def list_packages(self, third_party_only=False):
        args = ["pm", "list", "packages"]
        if third_party_only:
            args.append("-3")
        out = self.shell(*args)
        return [l.split(":", 1)[1] for l in out.splitlines() if l.startswith("package:")]

    def clear_app_data(self, package):
        return self.shell("pm", "clear", package)

    def grant(self, package, permission):
        return self.shell("pm", "grant", package, permission)

    # --- activity / app lifecycle ---

    def launch_app(self, package, activity=None):
        if activity:
            component = activity if "/" in activity else f"{package}/{activity}"
            return self.shell("am", "start", "-W", "-n", component)
        return self.shell("monkey", "-p", package, "-c",
                          "android.intent.category.LAUNCHER", "1")

    def force_stop(self, package):
        return self.shell("am", "force-stop", package)

    # Field name varies by Android version: mResumedActivity (legacy),
    # topResumedActivity / ResumedActivity (API 29+).
    _RESUMED_RE = re.compile(
        r"(?:mResumedActivity|topResumedActivity|ResumedActivity)\s*[:=]\s*"
        r"ActivityRecord\{[^ ]+ [^ ]+ ([^ }]+)"
    )
    _FOCUS_RE = re.compile(r"mCurrentFocus=Window\{[^ ]+ [^ ]+ ([^ }]+)")

    def current_activity(self):
        """Returns the foreground component as 'package/activity', or None."""
        out = self.shell("dumpsys", "activity", "activities", check=False)
        match = self._RESUMED_RE.search(out)
        if match:
            return match.group(1)
        # Fallback for devices/states where the activity dump omits it.
        out = self.shell("dumpsys", "window", check=False)
        match = self._FOCUS_RE.search(out)
        return match.group(1) if match else None

    def current_package(self):
        component = self.current_activity()
        return component.split("/", 1)[0] if component else None

    # --- input ---

    def tap(self, x, y):
        self.shell("input", "tap", int(x), int(y))

    def swipe(self, x1, y1, x2, y2, duration_ms=300):
        self.shell("input", "swipe", int(x1), int(y1), int(x2), int(y2), int(duration_ms))

    def type_text(self, text):
        self.shell("input", "text", text.replace(" ", "%s"))

    def keyevent(self, key):
        self.shell("input", "keyevent", str(key))

    def wake(self):
        """Turn the display on. KEYCODE_WAKEUP, not POWER, so it never sleeps
        a device that was already awake."""
        self.shell("input", "keyevent", "224")   # KEYCODE_WAKEUP

    def sleep_screen(self):
        self.shell("input", "keyevent", "223")   # KEYCODE_SLEEP

    def dismiss_keyguard(self):
        """Swipe away the lock screen. A device with a PIN or pattern will stop
        at the credential prompt instead."""
        self.shell("wm", "dismiss-keyguard", check=False)

    # --- files & capture ---

    def push(self, local, remote):
        return self.run("push", local, remote, timeout=300).stdout

    def pull(self, remote, local):
        return self.run("pull", remote, local, timeout=300).stdout

    def screenshot(self, local_path):
        proc = subprocess.run(
            [self._binary, "-s", self.serial, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=self.timeout,
        )
        if proc.returncode != 0 or not proc.stdout:
            raise AdbError(["exec-out", "screencap"], proc.returncode,
                           "", proc.stderr.decode(errors="replace"))
        with open(local_path, "wb") as fh:
            fh.write(proc.stdout)
        return local_path

    def bugreport(self, local_path, timeout=300):
        """Capture a full device bugreport zip.

        Slow and large — roughly 70s and 10MB on a Pixel 6a — so callers should
        take at most one per run rather than one per failure.
        """
        proc = self.run("bugreport", str(local_path), timeout=timeout, check=False)
        path = Path(local_path)
        if proc.returncode != 0 or not path.exists() or path.stat().st_size == 0:
            raise AdbError(["bugreport", str(local_path)], proc.returncode,
                           proc.stdout, proc.stderr)
        return str(path)

    def screen_size(self):
        out = self.shell("wm", "size")
        match = re.search(r"(\d+)x(\d+)", out)
        if not match:
            raise AdbError(["wm", "size"], 0, out, "")
        return int(match.group(1)), int(match.group(2))
