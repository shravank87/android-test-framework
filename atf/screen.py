from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

DEFAULT_WAIT = 15


def by_id(resource_id):
    return (AppiumBy.ID, resource_id)


def by_text(text):
    return (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{text}")')


def by_text_contains(text):
    return (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().textContains("{text}")')


def by_desc(description):
    return (AppiumBy.ACCESSIBILITY_ID, description)


def by_class(class_name):
    return (AppiumBy.CLASS_NAME, class_name)


def by_xpath(expression):
    return (AppiumBy.XPATH, expression)


class Screen:
    """Base page object. Subclass per screen and expose named locators."""

    def __init__(self, driver, timeout=DEFAULT_WAIT):
        self.driver = driver
        self.timeout = timeout

    def _wait(self, timeout=None):
        return WebDriverWait(self.driver, timeout or self.timeout)

    def find(self, locator, timeout=None):
        return self._wait(timeout).until(EC.presence_of_element_located(locator))

    def find_all(self, locator, timeout=None):
        self._wait(timeout).until(EC.presence_of_element_located(locator))
        return self.driver.find_elements(*locator)

    def wait_visible(self, locator, timeout=None):
        return self._wait(timeout).until(EC.visibility_of_element_located(locator))

    def wait_clickable(self, locator, timeout=None):
        return self._wait(timeout).until(EC.element_to_be_clickable(locator))

    def wait_gone(self, locator, timeout=None):
        return self._wait(timeout).until(EC.invisibility_of_element_located(locator))

    def tap(self, locator, timeout=None):
        self.wait_clickable(locator, timeout).click()
        return self

    def type(self, locator, text, clear=True, timeout=None):
        element = self.wait_visible(locator, timeout)
        if clear:
            element.clear()
        element.send_keys(text)
        return self

    def text_of(self, locator, timeout=None):
        return self.wait_visible(locator, timeout).text

    def is_present(self, locator, timeout=3):
        try:
            self.find(locator, timeout=timeout)
            return True
        except (TimeoutException, NoSuchElementException):
            return False

    def scroll_to_text(self, text):
        selector = (
            'new UiScrollable(new UiSelector().scrollable(true))'
            f'.scrollIntoView(new UiSelector().textContains("{text}"))'
        )
        return self.driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, selector)

    def back(self):
        self.driver.back()
        return self
