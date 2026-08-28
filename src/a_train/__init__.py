"""A-Train: a deterministic, headless train simulator.

The public package surface is intentionally small. Module boundaries and
dependency direction are defined in ``docs/architectural.md`` §7.2:

* ``a_train.simulation``  -- simulation-time orchestration (no HTTP/TCP/YAML).
* ``a_train.domain``       -- train-world rules (imports nothing outward).
* ``a_train.adapters``     -- I/O boundaries that translate external data into
  core commands.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
