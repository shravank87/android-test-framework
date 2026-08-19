"""UI inspection and interaction over plain adb — no Appium, no Node, no agent APK.

`uiautomator dump` writes the on-screen view hierarchy to XML, which gives every
node's text, resource-id, class, checked state and pixel bounds. That is enough to
find a toggle by name, read whether it is on, and tap it — which covers most
system-UI testing without an Appium server in the loop.

Limits worth knowing: a dump is a still frame, so re-dump after every interaction;
dumps fail while the screen is off or mid-animation (handled here by retrying);
and WebViews and Compose surfaces may expose less detail than View-based screens.
"""
import re
import subprocess
import time
import xml.etree.ElementTree as ET

from .exceptions import AtfError

REMOTE_DUMP = "/sdcard/window_dump.xml"
_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class UiDumpError(AtfError):
    pass


class UiNode:
    """One node in the view hierarchy."""

    def __init__(self, element):
        self._el = element

    def __repr__(self):
        return (f"UiNode(class={self.class_name!r}, text={self.text!r}, "
                f"id={self.resource_id!r}, checked={self.checked})")

    def _attr(self, name, default=""):
        return self._el.get(name, default)

    @property
    def text(self):
        return self._attr("text")

    @property
    def content_desc(self):
        return self._attr("content-desc")

    @property
    def resource_id(self):
        return self._attr("resource-id")

    @property
    def short_id(self):
        """resource-id without the package prefix."""
        return self.resource_id.split("/")[-1] if self.resource_id else ""

    @property
    def class_name(self):
        return self._attr("class")

    @property
    def checkable(self):
        return self._attr("checkable") == "true"

    @property
    def checked(self):
        return self._attr("checked") == "true"

    @property
    def clickable(self):
        return self._attr("clickable") == "true"

    @property
    def enabled(self):
        return self._attr("enabled") == "true"

    @property
    def scrollable(self):
        return self._attr("scrollable") == "true"

    @property
    def bounds(self):
        match = _BOUNDS_RE.search(self._attr("bounds"))
        if not match:
            return None
        return tuple(int(g) for g in match.groups())

    @property
    def usable(self):
        """True when the node has real on-screen area worth tapping.

        A node scrolled to the fold can appear in the dump with inverted or
        zero-height bounds (e.g. [221,2360][482,2337]); its "center" would land
        somewhere useless, so callers should scroll it into view first.
        """
        box = self.bounds
        if not box:
            return False
        x1, y1, x2, y2 = box
        return x2 > x1 and y2 > y1

    @property
    def center(self):
        box = self.bounds
        if not box or not self.usable:
            return None
        x1, y1, x2, y2 = box
        return ((x1 + x2) // 2, (y1 + y2) // 2)


class UiTree:
    """A parsed snapshot of the screen."""

    def __init__(self, xml_text):
        self.xml = xml_text
        try:
            self._root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise UiDumpError(f"could not parse UI dump: {exc}") from exc

    def nodes(self):
        return (UiNode(el) for el in self._root.iter("node"))

    def find_all(self, text=None, text_contains=None, desc=None, resource_id=None,
                 class_contains=None, checkable=None, clickable=None):
        results = []
        for node in self.nodes():
            if text is not None and node.text != text:
                continue
            if text_contains is not None and text_contains not in node.text:
                continue
            if desc is not None and node.content_desc != desc:
                continue
            if resource_id is not None and resource_id not in (
                node.resource_id, node.short_id
            ):
                continue
            if class_contains is not None and class_contains not in node.class_name:
                continue
            if checkable is not None and node.checkable != checkable:
                continue
            if clickable is not None and node.clickable != clickable:
                continue
            results.append(node)
        return results

    def find(self, **kwargs):
        found = self.find_all(**kwargs)
        return found[0] if found else None

    def texts(self):
        return [n.text for n in self.nodes() if n.text]


class Ui:
    """Drives the screen through adb using uiautomator dumps."""

    def __init__(self, adb, settle=0.6):
        self.adb = adb
        self.settle = settle

    def dump(self, attempts=3):
        """Capture and parse the current view hierarchy."""
        last = ""
        for attempt in range(attempts):
            out = self.adb.shell("uiautomator", "dump", REMOTE_DUMP, check=False)
            if "dumped to" in out:
                xml_text = self._read_remote(REMOTE_DUMP)
                if xml_text.lstrip().startswith("<"):
                    return UiTree(xml_text)
                last = xml_text[:200]
            else:
                last = out
            time.sleep(0.7 * (attempt + 1))

        if "idle state" in last:
            raise UiDumpError(
                "uiautomator could not reach idle state, so the screen cannot be "
                "dumped. Some screen element is animating continuously (a spinner, "
                "progress card, video or live content). This is a hard limit of "
                "`uiautomator dump`: it waits for the UI to settle and gives up if "
                "it never does. Options: assert against this screen with adb/dumpsys "
                "instead, navigate past the animating element, or use Appium, whose "
                "UiAutomator2 driver can set waitForIdleTimeout to skip the wait."
            )
        raise UiDumpError(
            f"uiautomator dump failed after {attempts} attempts: {last!r}. "
            "Check that the screen is on and unlocked."
        )

    def _read_remote(self, path):
        proc = subprocess.run(
            [self.adb._binary, "-s", self.adb.serial, "exec-out", "cat", path],
            capture_output=True, timeout=self.adb.timeout,
        )
        return proc.stdout.decode("utf-8", errors="replace")

    # --- finding, with waiting ---

    def find(self, timeout=10, **selector):
        """Poll dumps until a node matches, or return None on timeout."""
        deadline = time.time() + timeout
        while True:
            node = self.dump().find(**selector)
            if node is not None:
                return node
            if time.time() >= deadline:
                return None
            time.sleep(0.5)

    def require(self, timeout=10, **selector):
        node = self.find(timeout=timeout, **selector)
        if node is None:
            raise UiDumpError(f"no node matched {selector} within {timeout}s")
        return node

    def is_present(self, timeout=3, **selector):
        return self.find(timeout=timeout, **selector) is not None

    # --- interaction ---

    def tap_node(self, node):
        point = node.center
        if point is None:
            raise UiDumpError(f"node has no usable bounds: {node!r}")
        self.adb.tap(*point)
        time.sleep(self.settle)
        return point

    def tap(self, timeout=10, **selector):
        return self.tap_node(self.require(timeout=timeout, **selector))

    def row_switch(self, title, timeout=10):
        """Find the Switch belonging to a settings row labelled `title`.

        Android renders the label and the switch as separate nodes, so the switch
        is located by matching the row's vertical band rather than by nesting.
        """
        tree = self.dump()
        label = tree.find(text=title)
        if label is None or label.bounds is None:
            return None
        _, ltop, _, lbottom = label.bounds
        for switch in tree.find_all(checkable=True):
            box = switch.bounds
            if not box:
                continue
            _, stop, _, sbottom = box
            # Overlapping vertical bands means they belong to the same row.
            if stop < lbottom and sbottom > ltop:
                return switch
        return None

    def set_switch(self, title, on, timeout=10):
        """Tap a row's switch until it reaches `on`. Returns the prior state."""
        switch = self.row_switch(title, timeout=timeout)
        if switch is None:
            raise UiDumpError(f"no switch found for row {title!r}")
        was = switch.checked
        if was == on:
            return was
        self.tap_node(switch)
        deadline = time.time() + timeout
        while time.time() < deadline:
            current = self.row_switch(title)
            if current is not None and current.checked == on:
                return was
            time.sleep(0.5)
        raise UiDumpError(
            f"switch {title!r} did not reach {'on' if on else 'off'} within {timeout}s"
        )

    def switch_state(self, title):
        switch = self.row_switch(title)
        return None if switch is None else switch.checked

    # --- text entry ---

    def type_text(self, text):
        """Type into whatever currently has focus.

        `adb shell` hands the command to the *device's* shell, so the text is
        single-quoted there — otherwise characters like & and ( are interpreted
        as shell syntax and the input is silently truncated.

        Non-ASCII is rejected rather than typed: `input text` mangles accented
        characters (cafe' for café) and throws a NullPointerException on some
        input, so failing loudly beats corrupting the field.
        """
        if not text.isascii():
            offenders = sorted({c for c in text if not c.isascii()})
            raise UiDumpError(
                f"`input text` cannot type non-ASCII characters {offenders}. "
                "Install an IME such as ADBKeyBoard for unicode entry, or set "
                "the value through `settings put` / an intent instead."
            )
        quoted = "'" + text.replace("'", "'\\''") + "'"
        self.adb.shell("input", "text", quoted)
        time.sleep(self.settle)

    def clear_field(self, max_presses=60):
        """Empty the focused field.

        Deletes are sent one keyevent at a time: passing several keycodes to a
        single `input keyevent` does NOT repeat the key — on Android 16 it injects
        literal text ("GT GT GT") into the field instead. The number of presses is
        bounded by the field's current length so this stays cheap.
        """
        length = 0
        for node in self.dump().nodes():
            if "EditText" in node.class_name and node.text:
                length = max(length, len(node.text))
        presses = min(max_presses, length + 2) if length else 4
        self.adb.keyevent(123)  # KEYCODE_MOVE_END — delete backwards from the end
        for _ in range(presses):
            self.adb.keyevent(67)  # KEYCODE_DEL
        time.sleep(self.settle)

    def type_into(self, text, clear=True, timeout=10, **selector):
        """Tap a field, optionally clear it, then type."""
        node = self.require(timeout=timeout, **selector)
        self.tap_node(node)
        if clear:
            self.clear_field()
        self.type_text(text)
        return node

    # --- scrolling ---

    def _scrollable_bounds(self):
        tree = self.dump()
        for node in tree.nodes():
            if node.scrollable and node.bounds:
                return node.bounds
        size = self.adb.screen_size()
        return (0, 0, size[0], size[1])

    def scroll(self, direction="down", fraction=0.6, duration_ms=350):
        """Swipe within the scrollable region. `down` reveals content below."""
        x1, y1, x2, y2 = self._scrollable_bounds()
        mid_x = (x1 + x2) // 2
        height = y2 - y1
        # Stay well inside the region so system edge gestures don't intercept.
        top = y1 + int(height * 0.25)
        bottom = y1 + int(height * 0.75)
        span = int((bottom - top) * fraction)
        if direction == "down":
            start_y, end_y = bottom, bottom - span
        else:
            start_y, end_y = top, top + span
        self.adb.swipe(mid_x, start_y, mid_x, end_y, duration_ms)
        time.sleep(self.settle)

    def scroll_to(self, max_swipes=12, direction="down", **selector):
        """Swipe until a node is on screen AND tappable.

        A match with unusable bounds means the row is clipped at the fold, so
        scrolling continues rather than handing back a node whose centre would
        land on the navigation bar.
        """
        node = self.dump().find(**selector)
        if node is not None and node.usable:
            return node
        seen_signature = None
        for _ in range(max_swipes):
            self.scroll(direction=direction)
            tree = self.dump()
            node = tree.find(**selector)
            if node is not None and node.usable:
                return node
            # Stop early once the screen stops changing (list has bottomed out),
            # unless a clipped match suggests one more swipe will reveal it.
            signature = tuple(tree.texts())
            if signature == seen_signature and node is None:
                return None
            seen_signature = signature
        return None

    def long_press(self, node, duration_ms=800):
        point = node.center
        if point is None:
            raise UiDumpError(f"node has no usable bounds: {node!r}")
        self.adb.swipe(point[0], point[1], point[0], point[1], duration_ms)
        time.sleep(self.settle)
        return point

    # --- navigation ---

    def back(self):
        self.adb.keyevent(4)
        time.sleep(self.settle)

    def home(self):
        self.adb.keyevent(3)
        time.sleep(self.settle)

    def open_settings(self, action=None):
        """Open the Settings app, optionally at a specific screen.

        `action` is an intent action such as
        android.settings.WIRELESS_SETTINGS or android.settings.DISPLAY_SETTINGS.
        """
        if action:
            self.adb.shell("am", "start", "-a", action)
        else:
            self.adb.shell("am", "start", "-n", "com.android.settings/.Settings")
        time.sleep(1.5)
