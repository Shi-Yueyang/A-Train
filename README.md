# A-Train Simulator

A-Train is a deterministic, headless train simulator written in Python. It owns
the simulated physical world, advances it on a fixed-step clock driven by a
single `SimulationCore` event loop, and communicates with external ATP
(Automatic Train Protection) processes over TCP using a text-based NDJSON
protocol. A thin browser client connects to a FastAPI REST/WebSocket API to
display live state and submit commands; the UI contains no simulation logic.
Manual stepping runs deterministically, so results do not depend on wall-clock
timing.

```bash
uv venv
uv pip install -e '.[dev]'

python -m a_train run --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/api/status

pytest
ruff check .
ruff format .
```

## Configuring ATP endpoints (Phases 3.1-3.2)

By default the server runs with no ATP connections. Configure one endpoint per
cab on the `run` command; each becomes a persistent reconnecting TCP client.
There is no handshake: the channel is live the moment TCP opens and content
flows immediately — `TRAIN_STATE` after every snapshot, with the current
BTM payload embedded in `equipment.btm`
delivery, inbound `ATP_COMMAND` driving the train, and `ERROR` reporting
(see [docs/atp-api.md](docs/atp-api.md)).

```bash
python -m a_train run --atp TRAIN001:1=127.0.0.1:19022 --atp TRAIN001:2=127.0.0.1:9102

# or a JSON file: {"atp_endpoints": [{"train_id": "TRAIN001", "cab_id": 1,
#                                     "host": "127.0.0.1", "port": 9101}]}
python -m a_train run --atp-config atp.json

curl http://127.0.0.1:8000/api/atp/status
# {"connections":[{"train_id":"TRAIN001","cab_id":1,"host":"127.0.0.1",
#                  "port":9101,"state":"READY","ready":true}]}
```

## Testing the manual web demo (Phase 2.5)

The browser demo is a manual verification tool and a test client of the public
API: it applies no restriction the API itself does not impose, and it exposes
a control for every operation the API offers, addressed the same way —
equipment by instance key (one panel per driving system, per door, per cab
BTM). It talks only to the REST and WebSocket APIs.

**Start it:**

```bash
python -m a_train run --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) in a browser. The page shows the initial
`STOPPED` state, simulation time `0.000`, and the configured train (`TRAIN001`,
cabs 1 and 2).

**Verify movement in `MANUAL` mode:**

1. Leave the mode on `MANUAL` and click **Run**.
2. Drag the **Drive demand** slider up (e.g. `1.00`) and click
   **Apply Demand**.
3. Set **Step (s)** to `0.50` and click **Step** a few times. Position and
   speed should increase each step; acceleration shows the applied drive
   demand.
4. Drag the **Drive demand** slider to a negative value (e.g. `-1.00`), click
   **Apply Demand**, then **Step**. Speed must drop toward zero and the legacy
   demand never moves a standing train backward.
5. Use the **Driving systems** fieldset: every cab's driving system gets its
   own panel. On the `driving_1` panel set **Mode** `Traction`, **Direction**
   `Forward`, raise **Acceleration**, click **Apply Handles**, then **Step**.
   The driving system overwrites the legacy drive demand while its mode is
   engaged; select **Direction** `Backward` to move the train to decreasing
   position, and set **Mode** `Brake` to oppose the current motion. The
   `driving_2` panel (cab 2 faces `backward`) moves the train the other way
   for the same handle positions, and both panels are live at the same time.
   Return a **Mode** to `Off` to give the legacy drive demand control again.
6. Click **Open** on a door panel, then engage a driving system and **Step**.
   The door state flips immediately, but the train still moves: doors do not
   gate dynamics in this version.

**Watch live state without refreshing:** the page keeps a WebSocket to `/ws`
(status badge near the controls: `live` / `connecting…` / `disconnected`; it
reconnects automatically). Select `REALTIME` (or `SCALED` with a multiplier),
click **Set Mode** then **Run**, and watch simulation time and train state
advance on their own — no manual refresh needed. **Pause** freezes the display.

**Verify errors don't change state:**

- Enter cab **9** (not configured) in the **Cab** dropdown and click
  **Apply Demand**. The message line shows `Rejected: cab 9 is not configured …` and the displayed state is unchanged.
- You can confirm the same result from the API:

  ```bash
  curl -X POST http://127.0.0.1:8000/api/trains/TRAIN001/commands \
    -H 'Content-Type: application/json' \
    -d '{"cab_id": 9, "drive_demand": 1.0}'
  # 400 {"detail":"cab 9 is not configured on TRAIN001"}
  ```

**Verify reset:**

- Move the train, open the doors, apply a negative drive demand, then click
  **Reset**. The page returns to `STOPPED`, time `0.000`, position `0.000`,
  drive demand `0.00`, doors `closed` — the configured
  initial state with control and equipment runtime state cleared.

**Optional — watch scaled time:** select `SCALED`, set a multiplier (e.g.
`2.0`), click **Set Mode** then **Run**. The WebSocket stream updates the page
while the simulation runs in real time (see "Watch live state" above).

**Automated checks:** the demo's static serving and API reachability are
covered by `pytest tests/test_web_demo.py`; the `/ws` snapshot stream is
covered by `pytest tests/test_websocket_state.py`. Run the whole suite with
`pytest`.
