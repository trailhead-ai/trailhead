---
description: Confirm the lore plugin is installed and show the resolved vault path. Use when you want to verify the lore plugin is active.
---

The lore plugin is installed and active.

Resolve and display the vault path by running:

```bash
python3 -c "
import os, sys
from pathlib import Path
raw = os.environ.get('LORE_VAULT', '')
vault = str(Path(raw).expanduser()) if raw else str(Path('~/lore').expanduser())
print('lore vault path:', vault)
"
```

Print the result to confirm the plugin is working. If `$LORE_VAULT` is set, it will show that value (with `~` expanded). Otherwise it shows `~/lore` expanded to the full path.
