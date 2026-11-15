from patchright.async_api import Error as PatchrightError, TimeoutError as PatchrightTimeout
from patchright.async_api import async_playwright as async_patchright
from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright


BROWSER_ERRORS = (PlaywrightError, PatchrightError)
BROWSER_TIMEOUTS = (PlaywrightTimeout, PatchrightTimeout)


def automation(name):
    if name == 'patchright':
        return async_patchright()
    if name == 'playwright':
        return async_playwright()
    raise ValueError('BROWSER_DRIVER must be playwright or patchright')
