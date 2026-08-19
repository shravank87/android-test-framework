class AtfError(Exception):
    pass


class AdbError(AtfError):
    def __init__(self, command, returncode, stdout, stderr):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"adb command failed ({returncode}): {' '.join(command)}\n"
            f"stdout: {stdout.strip()}\nstderr: {stderr.strip()}"
        )


class AdbTimeout(AtfError):
    pass


class NoDeviceError(AtfError):
    pass


class InstrumentationError(AtfError):
    pass
