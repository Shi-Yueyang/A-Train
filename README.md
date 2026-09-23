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
python -m a_train run --train-config train.json --host 127.0.0.1 --port 8001 
```

Open http://127.0.0.1:8001/ for the browser demo.

```bash
curl http://127.0.0.1:8001/api/status
```

## ATP

The simulator hosts one configured train. List the external ATP servers to
dial in the `atp` array of the train configuration file (see below).
Connections carry no cab binding: every peer receives the identical
whole-train `TRAIN_STATE` broadcast, and every peer may command any cab by
putting `cab_id` in its `ATP_COMMAND` messages.

```bash
python -m a_train run --train-config train.json --host 127.0.0.1 --port 8001
```

See [docs/atp-api.md](docs/atp-api.md) for the NDJSON protocol.

## Train configuration

Define the train, its equipment, and its ATP endpoints declaratively with a
required JSON file:

```bash
python -m a_train run --train-config train.json
```

The file describes the single supported train, its cabs, physics, and a flat
equipment list, plus an optional `atp` array of ATP server endpoints. For
example:

```json
{
	"train": {
		"train_id": "TRAIN001",
		"cabs": [
			{"cab_id": 1, "facing": "forward", "active": true},
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
			{"type": "stcs_atp_duo", "cab_id": 1}
		]
	},
	"atp": [
		{"host": "127.0.0.1", "port": 9101},
		{"host": "127.0.0.1", "port": 9102}
	]
}
```

Equipment keys are generated internally as unique REST addresses; cab-scoped
equipment must be unique by `(type, cab_id)`, which is also how ATP wire
messages address instances. The supported equipment types are `door`, `btm`,
`driving_system`, `stcs_atp_duo`, and `stcs_atp_solo`. Each equipment entry
may include `"enabled": false` to leave that equipment out of the installed
train; omitted `enabled` values default to `true`. Equipment configuration is
validated before the server starts. The optional `atp` array lists ATP
servers as `{host, port}` pairs to dial -- peers are not bound to cabs; a
missing or empty `atp` array runs the simulator with zero ATP connections.

## Checks

```bash
pytest
ruff check .
ruff format --check .
```

See [docs/web-api.md](docs/web-api.md) for REST/WebSocket details and
[docs/architectural.md](docs/architectural.md) for the design.
[docs/ashley.md](docs/ashley.md) is the superseded process-supervisor
design; [docs/supervision.md](docs/supervision.md) is the current decision:
native OS supervisors plus a thin cross-host control/monitoring shim (not
implemented).
