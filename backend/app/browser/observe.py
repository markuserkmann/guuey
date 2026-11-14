import html
import json
from dataclasses import dataclass, field
from urllib.parse import quote

from app.secrets import redact


def scrub(text, secrets=()):
    variants = set()
    for value in secrets:
        if value:
            variants.update((value, quote(value, safe=''), html.escape(value), json.dumps(value, ensure_ascii=False)[1:-1]))
    for value in sorted(variants, key=len, reverse=True):
        text = text.replace(value, '•••')
    return redact(text)


@dataclass
class Observation:
    id: int
    url: str
    title: str
    snapshot: str
    unchanged: bool = False
    text: str = field(default='', repr=False)

    def render(self):
        if self.unchanged:
            return f'[obs {self.id}] unchanged: {self.url}'
        return f'[obs {self.id}] {self.url} · {json.dumps(self.title, ensure_ascii=False)}\n{self.snapshot}'


async def observe(page, previous=None, secrets=()):
    text = scrub(await page.aria_snapshot(mode='ai'), secrets)
    url, title = scrub(page.url, secrets), scrub(await page.title(), secrets)
    snapshot = text if len(text) <= 12_000 else text[:12_000] + '\n… (truncated)'
    unchanged = previous is not None and (url, title, text) == (previous.url, previous.title, previous.text)
    return Observation(1 if previous is None else previous.id + 1, url, title, snapshot, unchanged, text)
