"""Train-world rules, independent of the time loop and external transports.

The ``domain`` package never imports ``simulation``, ``scenario``, ``adapters``,
or ``web`` (§7.2). Phase 3 implements the train aggregate, physics, equipment,
linear signals, and named digital I/O.
"""

from __future__ import annotations
