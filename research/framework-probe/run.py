#!/usr/bin/env python3
"""Run the framework probe. SPEC-v0.4 §7.

    python research/framework-probe/run.py --out results/2026-01-01.json --markdown TABLE.md

Adapters whose framework is not installed are skipped **by name**, and the skip appears in the
results file. Install the ones you want to measure — `langgraph`, `crewai`, `openai-agents`,
`autogen-agentchat` — and set `OPENAI_API_KEY`; none of them is installed by `ctrlrun` or by
any of its extras, and none ever will be.

The table reports **behaviour, not quality**. See `README.md`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from framework_probe.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
