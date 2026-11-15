import json
from types import SimpleNamespace
from unittest.mock import AsyncMock


class FakeControl:
    def __init__(self, page, name, role='button', **attrs):
        self.page, self.name, self.role = page, name, role
        self.attrs = {'tag': 'select' if role == 'combobox' else 'button', 'type': 'button', 'autocomplete': '', 'name': '', 'id': '',
                      'placeholder': '', 'label': name, 'formAction': '', 'href': '', **attrs}
        self.value = ''
        self.visible, self.enabled = True, True
        self.click_error = None

    async def is_visible(self):
        return self.visible

    async def is_enabled(self):
        return self.enabled

    async def aria_snapshot(self, **kwargs):
        return f'- {self.role} {json.dumps(self.name)}'

    async def evaluate(self, script, **kwargs):
        return {**self.attrs, 'role': self.role, 'label': self.name, 'visible': self.visible, 'enabled': self.enabled}

    async def fill(self, value):
        self.value = value

    async def input_value(self):
        return self.value

    async def select_option(self, label):
        self.value = label

    async def click(self, **kwargs):
        self.page.clicks += 1
        if self.name == 'Place order':
            self.page.text = 'Order #1001 received'
        if self.click_error:
            raise self.click_error


class FakePage:
    def __init__(self):
        self.url = 'https://shop.example.com/checkout'
        self.frames = []
        self.text = 'Checkout total €16.90'
        self.clicks = 0
        self.controls = {
            'e1': FakeControl(self, 'Place order', type='submit'),
            'e2': FakeControl(self, 'Password', 'textbox', tag='input', type='password'),
            'e3': FakeControl(self, 'Email', 'textbox', tag='input', type='email'),
        }
        self.password_count = 0
        self.context = SimpleNamespace(add_cookies=AsyncMock(), add_init_script=AsyncMock(), clear_cookies=AsyncMock(),
                                       storage_state=AsyncMock(return_value={'cookies': [], 'origins': []}))
        self.mouse = SimpleNamespace(wheel=AsyncMock())
        self.keyboard = SimpleNamespace(press=AsyncMock())
        self.wait_for_load_state = AsyncMock()
        self.screenshot = AsyncMock(return_value=b'jpeg')
        self.evaluate = AsyncMock()
        self.focused = self.controls['e1']

    def locator(self, selector):
        if selector.startswith('aria-ref='):
            return self.controls[selector[9:]]
        if selector == ':focus':
            return self.focused
        return SimpleNamespace(count=AsyncMock(return_value=self.password_count))

    async def aria_snapshot(self, **kwargs):
        return self.text + '\n' + '\n'.join(
            f'- {control.role} {json.dumps(control.name)} [ref={ref}]: {control.value}'
            for ref, control in self.controls.items())

    async def title(self):
        return 'Shop'

    async def goto(self, url, **kwargs):
        self.url = url

    def get_by_text(self, text, **kwargs):
        return SimpleNamespace(first=SimpleNamespace(wait_for=AsyncMock()))
