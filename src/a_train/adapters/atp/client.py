"""One reconnecting TCP client for one external ATP process (§4.2).

The simulator acts as the TCP client; ATP acts as the TCP server. Each client
connects to one ATP process for one train cab, sends ``HELLO``, handles
``HELLO_ACK``, and publishes ``TRAIN_STATE`` / ``BTM_RX`` messages. Phase 4
implements reconnection and the connection-state machine.
"""

from __future__ import annotations
