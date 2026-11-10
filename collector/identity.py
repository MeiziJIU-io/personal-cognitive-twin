"""Persistent random device identity; no hostname or account name is exported."""
import json
from pathlib import Path
from uuid import uuid4


def device_id(root):
    path = Path(root) / 'device.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as f:
            value = {'device_id': str(uuid4())}
            json.dump(value, f)
    except FileExistsError:
        pass
    return json.loads(path.read_text(encoding='utf-8'))['device_id']
