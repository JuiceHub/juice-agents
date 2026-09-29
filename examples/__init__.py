"""Runnable Showcase package.

The repository keeps the SDK in ``sdk/src`` rather than installing it into a
developer's environment.  Making that source root available here lets the
documented ``python -m examples.<showcase>`` commands work directly from a
fresh checkout, while installed SDK environments simply keep their existing
package resolution precedence.
"""

from __future__ import annotations

import sys
from pathlib import Path


_SDK_SRC = Path(__file__).resolve().parents[1] / "sdk" / "src"
if str(_SDK_SRC) not in sys.path:
    sys.path.insert(0, str(_SDK_SRC))
