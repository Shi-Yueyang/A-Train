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
python -m a_train run --host 127.0.0.1 --port 8001
```

Open http://127.0.0.1:8001/ for the browser demo.

```bash
curl http://127.0.0.1:8001/api/status
```

## ATP

The simulator hosts one train, identified as `TRAIN001` by default. Add one endpoint per cab:

```bash
python -m a_train run --host 127.0.0.1 --port 8001 --atp 1=127.0.0.1:9101 --atp 2=127.0.0.1:9102
```

Or use a JSON config file:

```bash
python -m a_train run --atp-config atp.json
```

See [docs/atp-api.md](docs/atp-api.md) for the NDJSON protocol.

## Train configuration

The default train is created in code. To define the train and its equipment
declaratively, pass a JSON file:

```bash
python -m a_train run --train-config train.json
```

The file describes the single supported train, its cabs, physics, and a flat
equipment list. For example:

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
			{"type": "door", "key": "left_door", "params": {"side": "left"}},
			{"type": "door", "key": "right_door", "params": {"side": "right"}},
			{"type": "btm", "key": "btm_front", "cab_id": 1},
			{"type": "stcs_atp", "key": "stcs_front", "cab_id": 1}
		]
	}
}
```

Equipment keys are unique addresses used by the REST API and snapshots. The
supported equipment types are `door`, `btm`, `driving_system`, and `stcs_atp`.
Equipment configuration is validated before the server starts. ATP endpoint
configuration remains separate and can be combined with `--train-config`.

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
