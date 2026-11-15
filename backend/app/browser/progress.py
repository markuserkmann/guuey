import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass, field

from app.browser.observe import foreground_text
from app.browser.refs import ELEMENT_REF
from app.browser.snapshot import without_refs


@dataclass
class BrowserProgress:
    attempts: deque = field(default_factory=lambda: deque(maxlen=16))
    blocked: int = 0
    outcomes: deque = field(default_factory=lambda: deque(maxlen=8))
    reads: deque = field(default_factory=lambda: deque(maxlen=12))

    def record_read(self, observation):
        signature = hashlib.sha256((observation.url + '\n' + without_refs(observation.snapshot)).encode()).hexdigest()
        self.reads.append(signature)
        return self.reads.count(signature)

    def key(self, tool, arguments, observation):
        if observation is None:
            return None
        def stable(text):
            return re.sub(rf'\s*\[(?:ref={ELEMENT_REF}|active)\]', '', text)

        refs = {ref: stable(line).strip()
                for line in observation.text.splitlines()
                for ref in re.findall(rf'\[ref=({ELEMENT_REF})\]', line)}

        def normalize(value):
            if hasattr(value, 'model_dump'):
                value = value.model_dump()
            if isinstance(value, dict):
                return {key: refs.get(item, item) if key == 'ref' and isinstance(item, str) else normalize(item)
                        for key, item in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value

        scope = observation.foreground or foreground_text(observation.text) or observation.text
        controls = '\n'.join(line for line in scope.splitlines() if re.match(
            r'''\s*- ['"]?(?:dialog|alertdialog|textbox|searchbox|combobox|button|option|checkbox|radio|switch|spinbutton|slider|heading)\b''', line)
            or re.match(r'''\s*- (?:generic|text)(?: \[[^\]]*\])*:\s*"?\d+"?\s*$''', line))
        target = []
        depth = None
        for line in observation.text.splitlines():
            indent = len(line) - len(line.lstrip())
            if depth is not None and line.strip() and indent <= depth:
                break
            if f"[ref={arguments.get('ref')}]" in line:
                depth = indent
            if depth is not None:
                target.append(line)
        text = '' if tool in {'open', 'back'} else stable(controls + '\n' + '\n'.join(target))
        data = json.dumps([tool, normalize(arguments), observation.url, text], sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()

    def repeats(self, key):
        return key is not None and self.attempts.count(key) >= 2

    def record(self, key, stalled, *, unsuccessful=None):
        self.outcomes.append(stalled if unsuccessful is None else unsuccessful)
        if not self.outcomes[-1]:
            self.blocked = 0
        # Successful clicks can still cycle between an open picker and the same selection.
        if key is not None:
            self.attempts.append(key)

    @property
    def exhausted(self):
        return len(self.outcomes) == 8 and sum(self.outcomes) >= 6
