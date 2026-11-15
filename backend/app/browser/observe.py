import asyncio
import html
import json
import re
import time
from dataclasses import dataclass, field, replace
from urllib.parse import quote

from playwright.async_api import Error

from app.secrets import redact
from app.browser.refs import ELEMENT_REF
from app.browser.snapshot import compact_snapshot, matching_lines


def foreground_text(text, region_refs=()):
    """Keep dialog and suggestion subtrees from the original reference-bearing snapshot."""
    lines = text.splitlines()
    regions, ancestors = set(), []
    for index, line in enumerate(lines):
        indent = len(line) - len(line.lstrip())
        while ancestors and ancestors[-1][0] >= indent:
            ancestors.pop()
        ancestors.append((indent, index, bool(re.match(r'''\s*- ['"]?region\b''', line))))
        if any(f'[ref={ref}]' in line for ref in region_refs):
            parent = next((item for item in reversed(ancestors) if item[2]), None)
            if parent:
                regions.add(parent[1])
    selected = []
    depth = None
    for index, line in enumerate(lines):
        indent = len(line) - len(line.lstrip())
        if depth is not None and line.strip() and indent <= depth:
            depth = None
        if depth is None and (re.match(r'''\s*- ['"]?(?:dialog|alertdialog|listbox)\b''', line)
                              or index in regions):
            depth = indent
        if depth is not None:
            selected.append(line)
    return '\n'.join(selected)


async def foreground_regions(page, text):
    refs = []
    for ref in re.findall(rf'''^\s*- ['"]?region\b[^\n]*\[ref=({ELEMENT_REF})\]''', text, re.M):
        try:
            # A subtree snapshot replaces Playwright's page-wide aria-ref lookup.
            active = await page.locator('aria-ref=' + ref).evaluate('''element =>
                element.matches('[role="region"][id*="portal" i]') &&
                [...element.querySelectorAll('button, a, input, textarea, select, h1, h2, h3, [role="button"], [role="combobox"]')]
                    .some(child => child.getClientRects().length && getComputedStyle(child).visibility !== 'hidden')
            ''', timeout=1000)
            if active:
                refs.append(ref)
        except Error:
            continue
    return refs


def foreground_excerpt(text):
    if len(text) > 8000:
        # A long dialog must not bury its own inputs and buttons in explanatory text.
        controls = '\n'.join(line for line in text.splitlines() if re.match(
            r'''\s*- ['"]?(?:dialog|alertdialog|textbox|searchbox|combobox|button|option|link|checkbox|radio|heading)\b''', line))
        return snapshot_excerpt(controls, limit=4000) + '\n' + snapshot_excerpt(text, limit=4000)
    return text


def snapshot_excerpt(text, query=None, offset=0, limit=12_000, ref=None):
    lines = text.splitlines()
    if query:
        selected = matching_lines(lines, query)
        if not selected:
            return f'No page text matches {json.dumps(query)}. Use inspect with another phrase or an empty query.'
        lines = [lines[index] for index in sorted(selected)]
    lines = [line[start:start + 4000] for line in lines for start in range(0, max(1, len(line)), 4000)]
    if offset >= len(lines):
        return 'End of page excerpt. Use inspect with offset=0 or another query.'
    output, size = [], 0
    for index in range(offset, len(lines)):
        line = lines[index]
        if output and size + len(line) + 1 > limit:
            break
        output.append(line)
        size += len(output[-1]) + 1
    else:
        index = len(lines)
    if index < len(lines):
        scope = f', ref={json.dumps(ref)}' if ref else ''
        output.append(f'… (truncated; inspect(query={json.dumps(query or "")}, offset={index}{scope}, refresh=false) for more)')
    return '\n'.join(output)


def subtree_text(text, ref):
    selected, ancestors, context, depth = [], [], [], None
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        if depth is not None and line.strip() and indent <= depth:
            break
        while ancestors and ancestors[-1][0] >= indent:
            ancestors.pop()
        if f'[ref={ref}]' in line:
            depth = indent
            context = [parent for _, parent in ancestors]
        if depth is not None:
            selected.append(line)
        ancestors.append((indent, line))
    if not selected:
        return f'Reference {ref} is absent. Inspect with refresh=true for current references.'
    return ('Ancestor context (not expanded):\n' + '\n'.join(context)
            + f'\nSubtree for {ref} ({len(selected)} lines before excerpt limits):\n' + '\n'.join(selected))


def unreadable_frames(text):
    pending, depth, readable = [], None, False
    for line in [*text.splitlines(), '- end']:
        indent = len(line) - len(line.lstrip())
        if depth is not None and indent <= depth:
            if not readable:
                pending.append(frame_ref)
            depth = None
        match = re.match(rf'\s*- iframe\b[^\n]*\[ref=({ELEMENT_REF})\]', line)
        if match:
            depth, frame_ref, readable = indent, match[1], False
        elif depth is not None:
            content = re.sub(r'^\s*- [\w-]+|\[[^\]]*\]', '', line).strip(' :".…')
            if content.casefold() not in {'loading', 'please wait'} and any(char.isalnum() for char in content):
                readable = True
    return pending


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
    foreground: str = field(default='', repr=False)
    blocks_background: bool = False

    def render(self):
        if self.unchanged:
            return f'[obs {self.id}] unchanged: {self.url}'
        return f'[obs {self.id}] {self.url} · {json.dumps(self.title, ensure_ascii=False)}\n{self.snapshot}'


def reframe(observation, *, query=None, offset=0, ref=None):
    text = subtree_text(observation.text, ref) if ref else observation.text
    text = compact_snapshot(text)
    snapshot = snapshot_excerpt(text, query, offset, ref=ref)
    if observation.foreground and not (ref and f'[ref={ref}]' in observation.foreground):
        primary = foreground_excerpt(compact_snapshot(observation.foreground))
        snapshot = ('Foreground dialogs and suggestions (resolve these before background controls):\n'
                    + primary + '\nBackground page (may be covered by the foreground):\n'
                    + snapshot_excerpt(text, query, offset, limit=max(1000, 11500 - len(primary)), ref=ref))
    return replace(observation, id=observation.id + 1, snapshot=snapshot,
                   unchanged=snapshot == observation.snapshot)


async def observe(page, previous=None, secrets=(), *, query=None, offset=0, ref=None, wait_for_frames=False):
    text = scrub(await page.aria_snapshot(mode='ai'), secrets)
    if wait_for_frames:
        deadline = time.monotonic() + 2
        while unreadable_frames(text) and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            text = scrub(await page.aria_snapshot(mode='ai'), secrets)
    url, title = scrub(page.url, secrets), scrub(await page.title(), secrets)
    region_refs = await foreground_regions(page, text)
    foreground = foreground_text(text, region_refs)
    blocks_background = bool(region_refs or re.search(r'''^\s*- ['"]?(?:dialog|alertdialog)\b''', text, re.M))
    current = reframe(Observation(0 if previous is None else previous.id, url, title, '',
                                  text=text, foreground=foreground, blocks_background=blocks_background),
                      query=query, offset=offset, ref=ref)
    current.unchanged = previous is not None and (url, title, text, current.snapshot) == (
        previous.url, previous.title, previous.text, previous.snapshot)
    return current
