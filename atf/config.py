import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


@dataclass
class AppConfig:
    package: str = ""
    activity: str = ""
    apk_path: str = ""
    test_apk_path: str = ""
    instrumentation_runner: str = ""


@dataclass
class AppiumConfig:
    server_url: str = "http://127.0.0.1:4723"
    automation_name: str = "UiAutomator2"
    new_command_timeout: int = 120
    implicit_wait: int = 0
    extra_capabilities: dict = field(default_factory=dict)


@dataclass
class Settings:
    app: AppConfig = field(default_factory=AppConfig)
    appium: AppiumConfig = field(default_factory=AppiumConfig)
    artifacts_dir: Path = Path("artifacts")
    capture_logcat: bool = True
    screenshot_on_failure: bool = True
    reinstall_app: bool = False
    clear_data_between_tests: bool = True


def _env_override(settings):
    env = os.environ
    if "ATF_APP_PACKAGE" in env:
        settings.app.package = env["ATF_APP_PACKAGE"]
    if "ATF_APP_ACTIVITY" in env:
        settings.app.activity = env["ATF_APP_ACTIVITY"]
    if "ATF_APK_PATH" in env:
        settings.app.apk_path = env["ATF_APK_PATH"]
    if "ATF_TEST_APK_PATH" in env:
        settings.app.test_apk_path = env["ATF_TEST_APK_PATH"]
    if "ATF_INSTRUMENTATION_RUNNER" in env:
        settings.app.instrumentation_runner = env["ATF_INSTRUMENTATION_RUNNER"]
    if "ATF_APPIUM_URL" in env:
        settings.appium.server_url = env["ATF_APPIUM_URL"]
    if "ATF_ARTIFACTS_DIR" in env:
        settings.artifacts_dir = Path(env["ATF_ARTIFACTS_DIR"])
    return settings


def load_settings(path=None):
    data = {}
    if path:
        path = Path(path)
        if path.exists():
            if yaml is None:
                raise RuntimeError("PyYAML is required to read config files")
            data = yaml.safe_load(path.read_text()) or {}

    settings = Settings(
        app=AppConfig(**data.get("app", {})),
        appium=AppiumConfig(**data.get("appium", {})),
    )
    for key in ("artifacts_dir", "capture_logcat", "screenshot_on_failure",
                "reinstall_app", "clear_data_between_tests"):
        if key in data:
            setattr(settings, key, data[key])
    settings.artifacts_dir = Path(settings.artifacts_dir)
    return _env_override(settings)
