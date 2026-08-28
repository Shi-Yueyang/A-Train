"""Train aggregate and stable per-step update entry point (§3).

The train model owns physical state and is responsible for physical behaviour.
It must not know the internal implementation of ATP. Phase 3 implements the
train aggregate; ``physics.py`` performs the actual calculations.
"""

from __future__ import annotations
