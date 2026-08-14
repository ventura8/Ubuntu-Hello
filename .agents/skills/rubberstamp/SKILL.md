---
name: rubberstamp
description: >-
  Create or enable Ubuntu Hello rubberstamp (post-auth liveness) plugins under
  ubuntu-hello/src/rubberstamps/. Use when adding nod/hotkey-style checks.
---

# Custom Rubberstamp Hooks

Rubberstamps are post-verification liveness checks.

## Add a plugin

1. Create `ubuntu-hello/src/rubberstamps/yourstamp.py`.
2. Inherit from `RubberStamp` and implement `declare_config` and `run`:

```python
from rubberstamps import RubberStamp

class yourstamp(RubberStamp):
    def declare_config(self):
        self.options["min_confidence"] = 0.85

    def run(self):
        self.set_ui_text("Look straight and smile", self.UI_TEXT)
        # ret, frame = self.video_capture.read_frame()
        return True  # False aborts auth (exit 15)
```

3. Enable in `/etc/ubuntu-hello/config.ini`:

```ini
[rubberstamps]
enabled = true
stamp_rules =
    yourstamp 10s failsafe min_confidence=0.90
```

## Built-ins

* `nod.py` — nose-tracking nod / shake
* `hotkey.py` — confirm with a key

## Tests

`tests/test_rubberstamps.py`. Debug with `verbose_stamps = true` in `[debug]`.
