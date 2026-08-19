import subprocess
import threading


class LogcatRecorder:
    """Streams logcat from a device into a file for the duration of a test."""

    def __init__(self, adb, output_path, log_filter=None):
        self.adb = adb
        self.output_path = str(output_path)
        self.log_filter = log_filter or []
        self._proc = None
        self._fh = None
        self._reader = None

    def start(self):
        self.adb.run("logcat", "-c", check=False)
        self._fh = open(self.output_path, "w", encoding="utf-8", errors="replace")
        self._proc = subprocess.Popen(
            [self.adb._binary, "-s", self.adb.serial, "logcat", "-v", "threadtime",
             *self.log_filter],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        return self

    def _pump(self):
        for line in self._proc.stdout:
            self._fh.write(line)
        self._fh.flush()

    def stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._reader:
            self._reader.join(timeout=5)
        if self._fh:
            self._fh.close()
        return self.output_path

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
