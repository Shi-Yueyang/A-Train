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

```bash
uv venv
uv pip install -e '.[dev]'
```

## Run

```bash
python -m a_train run --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ for the browser demo.

```bash
curl http://127.0.0.1:8000/api/status
```

## ATP

The simulator hosts one train, identified as `TRAIN001` by default. Add one endpoint per cab:

```bash
python -m a_train run --host 127.0.0.1 --port 8000 --atp 1=127.0.0.1:9101 --atp 2=127.0.0.1:9102
```

Or use a JSON config file:

```bash
python -m a_train run --atp-config atp.json
```

See [docs/atp-api.md](docs/atp-api.md) for the NDJSON protocol.

## Checks

```bash
pytest
ruff check .
ruff format --check .
```

See [docs/web-api.md](docs/web-api.md) for REST/WebSocket details and
[docs/architectural.md](docs/architectural.md) for the design.
