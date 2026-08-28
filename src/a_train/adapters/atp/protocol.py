"""NDJSON encode/decode and protocol-message validation (§4.2, §4.3).

The protocol uses TCP + NDJSON (one JSON object per line). The newline provides
application-level message framing. Validated message types include ``HELLO``,
``HELLO_ACK``, ``TRAIN_STATE``, ``ATP_STATE``, ``BTM_RX``, ``HEARTBEAT``,
``HEARTBEAT_ACK``, and ``ERROR``. Phase 4 implements validation and framing.
"""

from __future__ import annotations
