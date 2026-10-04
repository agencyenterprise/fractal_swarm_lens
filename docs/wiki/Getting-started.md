# Getting started

## Install and open the explorer

Requirements: Python 3.11+ and Git. Python 3.12 is the tested version for the CrewAI integration.

```sh
git clone https://github.com/agencyenterprise/fractal_swarm_lens.git
cd fractal_swarm_lens
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[web]'
swarm-lens --data data --port 8765
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell. Open <http://127.0.0.1:8765>. Stop the server with Ctrl+C. The browser assets are bundled; Node.js is only needed to rebuild the frontend.

## Load a trace

Choose **⋯ → Import trace**, paste a transcript, and inspect it in Timeline. For structured examples with recorded interactions and memories, use the [medical debate import recipe](Examples-and-datasets).

An empty run picker means the selected workspace has no imported runs. The `--data` argument selects a directory containing SQLite history, artifacts, and supporting stores. Always use the same directory when restarting.

## Optional analysis

```sh
python -m pip install -e '.[mast]'
```

Set `OPENAI_API_KEY` in your shell or a local `.env`. Restart the server, choose **⋯ → MAST trace analysis → Analyze with MAST**, and inspect the free size preview before submitting. Submitting sends the selected trace to the configured provider and incurs model usage. Saved playback and trace import do not require model calls.

## Next

[Explore traces](Explore-traces) · [Live collection](Live-collection-and-branching) · [Troubleshooting](Troubleshooting)
