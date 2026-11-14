import json
import base64
import os
import re
from urllib.parse import quote


SECRET_ENV_NAMES = (
    'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'EXA_API_KEY', 'AGENT_API_TOKEN',
    'OXY_AB_PASSWORD', 'BROWSER_SESSION_KEY', 'OXYLAB_KEY', 'OXYLABS_WEB_API_KEY',
)


def redact(text):
    value = str(text)
    secrets = set()
    username, password = os.getenv('OXY_AB_USERNAME'), os.getenv('OXY_AB_PASSWORD')
    if username and password:
        credentials = f'{username}:{password}'.encode()
        secrets.update((base64.b64encode(credentials).decode(), base64.urlsafe_b64encode(credentials).decode()))
    for name in SECRET_ENV_NAMES:
        secret = os.getenv(name)
        if secret:
            secrets.update((secret, quote(secret, safe=''), json.dumps(secret)[1:-1]))
    for secret in sorted(secrets, key=len, reverse=True):
        value = value.replace(secret, '[redacted]')
    return re.sub(r'(wss?://)[^\s/]*@', r'\1[redacted]@', value)
