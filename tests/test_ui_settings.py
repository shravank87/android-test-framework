import pytest

from atf import by_text
from tests.screens.settings_screen import SettingsScreen


@pytest.mark.ui
def test_settings_launches(driver):
    screen = SettingsScreen(driver)
    assert screen.is_present(by_text("Settings"), timeout=10)


@pytest.mark.ui
def test_open_about_phone(driver):
    screen = SettingsScreen(driver)
    screen.open_entry("About phone")
    assert screen.is_present(by_text("Model"), timeout=10)


@pytest.mark.ui
def test_back_returns_to_root(driver):
    screen = SettingsScreen(driver)
    screen.open_entry("About phone")
    screen.back()
    assert screen.is_present(by_text("Network & internet"), timeout=10)
