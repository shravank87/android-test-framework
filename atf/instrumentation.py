import re
from dataclasses import dataclass, field

from .exceptions import InstrumentationError

STATUS_CODE = {0: "passed", 1: "started", -1: "error", -2: "failed", -3: "ignored"}


@dataclass
class InstrumentationCase:
    clazz: str
    test: str
    status: str
    stream: str = ""

    @property
    def name(self):
        return f"{self.clazz}#{self.test}"


@dataclass
class InstrumentationResult:
    cases: list = field(default_factory=list)
    raw: str = ""

    @property
    def failures(self):
        return [c for c in self.cases if c.status in ("failed", "error")]

    @property
    def passed(self):
        return [c for c in self.cases if c.status == "passed"]

    def assert_all_passed(self):
        if self.failures:
            detail = "\n\n".join(f"{c.name}\n{c.stream.strip()}" for c in self.failures)
            raise AssertionError(
                f"{len(self.failures)} instrumented test(s) failed:\n\n{detail}"
            )


def parse_instrument_output(raw):
    """Parse `am instrument -r` protocol output into per-test results."""
    result = InstrumentationResult(raw=raw)
    current = {}
    stream_lines = []
    in_stream = False

    for line in raw.splitlines():
        if line.startswith("INSTRUMENTATION_STATUS: "):
            body = line[len("INSTRUMENTATION_STATUS: "):]
            key, _, value = body.partition("=")
            if key == "stream":
                in_stream = True
                stream_lines = [value]
            else:
                in_stream = False
                current[key] = value
        elif line.startswith("INSTRUMENTATION_STATUS_CODE: "):
            code = int(line.split(": ", 1)[1].strip())
            in_stream = False
            if code != 1 and current.get("class") and current.get("test"):
                result.cases.append(
                    InstrumentationCase(
                        clazz=current["class"],
                        test=current["test"],
                        status=STATUS_CODE.get(code, f"unknown({code})"),
                        stream="\n".join(stream_lines),
                    )
                )
                current = {}
                stream_lines = []
        elif in_stream:
            stream_lines.append(line)

    return result


def run_instrumentation(adb, runner, test_class=None, test_package=None,
                        extra_args=None, timeout=1800):
    """Run an on-device instrumentation suite via `am instrument`.

    runner: "com.example.test/androidx.test.runner.AndroidJUnitRunner"
    """
    args = ["am", "instrument", "-w", "-r"]
    if test_class:
        args += ["-e", "class", test_class]
    if test_package:
        args += ["-e", "package", test_package]
    for key, value in (extra_args or {}).items():
        args += ["-e", key, str(value)]
    args.append(runner)

    proc = adb.run("shell", *args, timeout=timeout, check=False)
    raw = proc.stdout + proc.stderr
    if "INSTRUMENTATION_STATUS" not in raw and "INSTRUMENTATION_RESULT" not in raw:
        raise InstrumentationError(
            f"instrumentation produced no results for {runner}:\n{raw.strip()}"
        )
    if re.search(r"INSTRUMENTATION_(RESULT|FAILED).*(unable to find instrumentation|"
                 r"Unable to find instrumentation)", raw, re.IGNORECASE):
        raise InstrumentationError(f"runner not found on device: {runner}")
    return parse_instrument_output(raw)
