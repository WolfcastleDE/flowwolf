# Contributing to Flowwolf

Thanks for helping! Bug reports, integration requests and pull requests are all welcome.

## Before you start

- **Bugs and requests** go into [issues](https://github.com/WolfcastleDE/flowwolf/issues/new/choose). Pick the
  template that fits; an exported workflow that shows the problem saves a lot of guessing.
- **Questions and ideas** fit better in [discussions](https://github.com/WolfcastleDE/flowwolf/discussions).
- **Security issues** are reported privately, see [SECURITY.md](SECURITY.md).

## Development setup

You need Docker. The scripts build inside the official Rust image, so no local toolchain is required.

```bash
./dev.sh test                 # cargo test inside rust:1-bookworm (cached volumes)
docker build -t flowwolf .    # the production image
docker run --rm -p 5678:5678 flowwolf
```

With a local Rust toolchain (1.85+), `cargo run` starts the server on http://localhost:5678.

## Where things live

| Path | What |
|---|---|
| `src/engine.rs` | Workflow execution: items, branches, waits, sub-workflows |
| `src/js.rs` | Expressions and the Code node (QuickJS sandbox) |
| `src/nodes/` | Hand-written nodes (`core.rs`, `integrations.rs`, `cluster.rs` for AI, …) |
| `src/nodes/integrations.json` | Generated REST integrations — edit `tools/gen_integrations.py`, then run it |
| `src/api.rs`, `src/users.rs` | REST API (`/rest`, `/api/v1`), webhooks, forms, user management |
| `web/app/` | The editor (plain JavaScript, no build step) |
| `web/index.html` | The website |
| `tests/` | End-to-end suites against real containers; `tests/run_all.sh` runs them all |

## Adding or extending an integration

Most integrations are declarative: add operations to `tools/gen_integrations.py` and run
`python tools/gen_integrations.py`. Keep **n8n's parameter names** (resource, operation, field names) so exported
n8n workflows run unchanged. Add a contract check to `tests/e2e_contracts.py` that compares the request with what
the real API expects. Operations that need several requests or binary data get a few lines of Rust next to the
other hand-written nodes.

## Pull requests

- One topic per pull request, with a short description of what and why.
- `cargo test` and the end-to-end suite you touched must pass.
- Code comments are in English. UI changes are checked in light and dark mode and at phone width.
- By contributing you agree that your work is published under the project's [Sustainable Use License](LICENSE) and that
  Wolfscastle.de may also license it under other terms in the future.
