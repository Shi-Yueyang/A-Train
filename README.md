# A-Train Simulator

A-Train is a deterministic, headless train simulator written in Python. It owns
the simulated physical world, advances it on a fixed-step clock driven by a
single `SimulationCore` event loop, and communicates with external ATP
(Automatic Train Protection) processes over TCP using a text-based NDJSON
protocol. A thin browser client connects to a FastAPI REST/WebSocket API to
display live state and submit commands; the UI contains no simulation logic.
Scenarios are YAML files with scheduled events and are run deterministically in
manual stepping mode so results do not depend on wall-clock timing.

```bash
uv venv
uv pip install -e '.[dev]'

python -m a_train run --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/api/status

python -m a_train test scenarios/basic.yaml

pytest
ruff check .
ruff format .
```
