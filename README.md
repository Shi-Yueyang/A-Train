# A-Train Simulator

A-Train is a deterministic, headless single-train simulator written in Python. It owns
the simulated physical world, advances it on a fixed-step clock driven by a
single `SimulationCore` event loop, and communicates with external ATP
(Automatic Train Protection) processes over TCP using a text-based NDJSON
protocol. A thin browser client connects to a FastAPI REST/WebSocket API to
display live state and submit commands; the UI contains no simulation logic.
Manual stepping runs deterministically, so results do not depend on wall-clock
timing.

## Setup

Requires Python 3.10+.

With [uv](https://docs.astral.sh/uv/):

```bash
uv venv
uv pip install -e '.[dev]'
```

Without uv, using the standard library `venv` and `pip`:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
```

Run subsequent commands (`python -m a_train ...`, `pytest`, `ruff`, ...) inside
the activated virtual environment.

## Run

```bash
python -m a_train run --train-config train.json --host 127.0.0.1 --port 8101 --log-level DEBUG
```

Use `--log-level DEBUG` to show debug records, including BTM API payload logs.

Open http://127.0.0.1:8101/ for the browser demo.

```bash
curl http://127.0.0.1:8101/api/status
```

## ATP

The simulator hosts one configured train and listens for external ATP clients
on the address or addresses in the `atp` array of the train configuration
file (see below). ATP connects to A-Train. Connections carry no cab binding:
every connected peer receives the identical whole-train `TRAIN_STATE`
broadcast, and every peer may command any cab by putting `cab_id` in its
`ATP_COMMAND` messages.

```bash
python -m a_train run --train-config train.json --host 127.0.0.1 --port 8101
```

See [docs/atp-api.md](docs/atp-api.md) for the NDJSON protocol.

## Train configuration

Define the train, its equipment, and its ATP endpoints declaratively with a
required JSON file:

```bash
python -m a_train run --train-config train.json
```

The file describes the single supported train, its cabs, physics, and a flat
equipment list, plus an optional `atp` array of local ATP listener addresses. For
example:

```json
{
	"train": {
		"train_id": "TRAIN001",
		"cabs": [
			{"cab_id": 1, "facing": "forward", "active": true, "key_inserted": true},
			{"cab_id": 2, "facing": "backward", "active": false}
		],
		"physics": {
			"max_traction_accel": 1.5,
			"max_decel": 2.0,
			"initial_position": 0.0,
			"initial_speed": 0.0
		},
		"equipment": [
			{"type": "door", "params": {"side": "left"}},
			{"type": "door", "params": {"side": "right"}},
			{"type": "btm", "cab_id": 1},
			{"type": "stcs_atp_duo", "cab_id": 1},
			{"type": "cbtc", "cab_id": 1},
			{"type": "switch_box", "cab_id": 1}
		]
	},
	"atp": [
		{"host": "127.0.0.1", "port": 8102}
	]
}
```

Cab entries may set `key_inserted` to `true` to start with the key inserted;
omitted values default to `false`. Reset restores each cab's configured key
state. Equipment keys are generated internally as unique REST addresses; cab-scoped
equipment must be unique by `(type, cab_id)`, which is also how ATP wire
messages address instances. The `cbtc` equipment stores a cab-scoped
`is_cbtc_authorized` boolean, initially `false` unless set in `params`, and can
be updated through its equipment endpoint. It feeds the same cab's switch
box `cbtc_authorized` state through an equipment intent; a physical link cut
can interrupt that connection. The supported equipment types are `door`, `btm`,
`cbtc`, `driving_system`, `stcs_atp_duo`, `stcs_atp_solo`, and `switch_box`.
Each equipment entry may include `"enabled": false` to leave that equipment
out of the installed train;
omitted `enabled` values default to `true`. Equipment configuration is
validated before the server starts. Each optional `atp` entry is a `{host, port}`
address for A-Train to bind; ATP clients connect to that address. Peers are not
bound to cabs. A missing or empty `atp` array starts without an ATP listener.

## Checks

```bash
pytest
ruff check .
ruff format --check .
```

See [docs/web-api.md](docs/web-api.md) for REST/WebSocket details,
[docs/architectural.md](docs/architectural.md) for the design, and
[docs/btm-telegram.md](docs/btm-telegram.md) for the JSON BTM telegram encoder (CTCS frame)
(`POST /api/trains/{id}/equipment/btm_1` with a `telegram` object).
[docs/ashley.md](docs/ashley.md) is the superseded process-supervisor
design; [docs/supervision.md](docs/supervision.md) is the current decision:
native OS supervisors plus a thin cross-host control/monitoring shim (not
implemented).
