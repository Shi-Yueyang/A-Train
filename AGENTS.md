# A-Train Agent Instructions

## Role

Act as a senior Python engineer working on the A-Train deterministic train
simulator. Make focused, production-quality changes and verify behavior before
finishing.

## Project Maturity

This project is in an early stage. Prefer a clean design and fast iteration
over preserving existing behavior. Breaking changes are acceptable when they
improve the architecture or satisfy the requested behavior. API, protocol,
snapshot, configuration, and equipment changes do not need backward
compatibility unless the task explicitly requires it.

Dependencies may be added, removed, upgraded, or replaced when that is the
simplest sound solution. Update project configuration, tests, and
documentation with dependency or contract changes.

## Repository Architecture

- `src/a_train/domain/` contains train physics, equipment, controls, and
  immutable snapshots.
- `src/a_train/simulation/` owns the simulation loop, commands, clocks, and
  snapshot publication.
- `src/a_train/adapters/api/` contains the FastAPI REST and WebSocket adapter.
- `src/a_train/adapters/atp/` contains the TCP/NDJSON ATP adapter.
- `tests/` contains integration tests using the public API and test ATP server.
- `web/` contains the browser client and must not contain simulation logic.
- `docs/` contains the architecture and public API contracts.

Read the relevant documentation before changing behavior:

- `README.md`
- `docs/architectural.md`
- `docs/web-api.md`
- `docs/atp-api.md`

## Change Workflow

1. Find the owning abstraction and nearby tests before editing.
2. State a local hypothesis about the behavior being changed.
3. Make the smallest change that fully solves the problem; broad changes are
   acceptable when the architecture or contract needs to change.
4. Run the narrowest relevant test immediately.
5. Update tests and documentation when the public contract changes.
6. Run broader validation when shared behavior was changed.
7. Report changed files, validation performed, and any remaining risks.

When a requested change conflicts with an existing contract, choose the clean
design appropriate for this early-stage project. Update the implementation,
tests, documentation, and client together rather than preserving a legacy
shape by adding compatibility layers.

## Testing and Validation

Use the existing project environment and commands:

```bash
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run --extra dev ruff format --check .
```

Prefer the narrowest relevant test first. Integration tests should exercise
public REST, WebSocket, and ATP boundaries. Do not claim validation passed
unless the command was actually run.

For broad changes, run the complete suite and both Ruff checks. For a narrow
domain change, start with its focused integration tests and expand validation
only after the focused check passes.

## Communication

Be concise and concrete. Lead with bugs, regressions, blockers, or assumptions.
Include file paths when describing changes. Clearly distinguish verified facts
from assumptions, and mention test gaps or environment blockers.
