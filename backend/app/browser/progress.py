import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass, field


@dataclass
class BrowserProgress:
    attempts: deque = field(default_factory=lambda: deque(maxlen=16))
    blocked: int = 0

    def key(self, tool, arguments, observation):
        if observation is None:
            return None
        text = re.sub(r'\[ref=e\d+\]', '', observation.text)
        refs = {ref: re.sub(r'\[ref=e\d+\]', '', line).strip()
                for line in observation.text.splitlines()
                for ref in re.findall(r'\[ref=(e\d+)\]', line)}

        def normalize(value):
            if hasattr(value, 'model_dump'):
                value = value.model_dump()
            if isinstance(value, dict):
                return {key: refs.get(item, item) if key == 'ref' and isinstance(item, str) else normalize(item)
                        for key, item in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value

        data = json.dumps([tool, normalize(arguments), observation.url, text], sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()

    def repeats(self, key):
        return key is not None and self.attempts.count(key) >= 2

    def record(self, key, stalled):
        self.blocked = 0
        if key is not None and stalled:
            self.attempts.append(key)
