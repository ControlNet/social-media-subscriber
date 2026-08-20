# Project Instructions

## Environment

- Use Pixi exclusively for this Python project.
- Use the Pixi environment named `default` at `.pixi/envs/default`.
- Use Python 3.13 from that environment; do not use system Python, pip, Poetry, or uv.
- Run project commands with `pixi run <task>` from the repository root.
- Add runtime and development dependencies through `pixi.toml`, then commit the updated `pixi.lock`.
- Keep `pyrightconfig.json` pointed at `.pixi/envs/default` so imports and types resolve against the confirmed environment.

## Engineering

- Keep code, comments, and identifiers in English.
- Use test-driven development and retain strict Ruff, basedpyright, and pytest gates.
- Never commit credentials, environment files, provider responses, generated artifacts, or `.omo/**` state.
