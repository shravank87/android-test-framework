import json
import urllib.error
import urllib.request

from appium import webdriver
from appium.options.android import UiAutomator2Options


def server_status(server_url, timeout=3):
    """Returns the Appium server's status payload, or None if unreachable."""
    try:
        with urllib.request.urlopen(
            f"{server_url.rstrip('/')}/status", timeout=timeout
        ) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def build_options(settings, serial):
    caps = {
        "platformName": "Android",
        "automationName": settings.appium.automation_name,
        "udid": serial,
        "deviceName": serial,
        "newCommandTimeout": settings.appium.new_command_timeout,
    }
    if settings.app.package:
        caps["appPackage"] = settings.app.package
    if settings.app.activity:
        caps["appActivity"] = settings.app.activity
        caps["appWaitActivity"] = "*"
    if settings.app.apk_path and settings.reinstall_app:
        caps["app"] = settings.app.apk_path
    caps.update(settings.appium.extra_capabilities)
    return UiAutomator2Options().load_capabilities(caps)


def create_driver(settings, serial):
    driver = webdriver.Remote(
        settings.appium.server_url, options=build_options(settings, serial)
    )
    if settings.appium.implicit_wait:
        driver.implicitly_wait(settings.appium.implicit_wait)
    return driver
