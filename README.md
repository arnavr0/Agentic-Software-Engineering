# Agentic Tester

Agentic Tester is a standalone proof of concept for mission-driven, AI-assisted browser testing. It accepts structured test missions, executes only their explicit action steps against a live web application with Playwright, and produces machine-readable results backed by concrete browser evidence.

This repository intentionally contains the tester subsystem only. It does not implement a coding agent or manager/orchestration layer. The future manager can consume the tester's mission and report models through the API layer planned in later phases.

## Current implementation

The foundation currently includes:

- Python 3.12+ packaging and development commands
- environment-backed settings for the LLM, browser, server, artifacts, and execution limits
- compact model-facing observations, bounded evidence, replay-isolated context, and optional oracle-only JPEG screenshots
- domain models for missions, observations, evidence, safety policy, results, sessions, and state fingerprints
- pluggable Gemini adapter with structured responses, multimodal page context, retries, and token accounting
- isolated Playwright browser contexts, semantic element references, screenshots, traces, console/network buffers, and state fingerprints
- sliding-window execution context with LLM or deterministic history summaries
- deterministic guardrails for risk, URL, input, repetition, and browser-error limits
- filesystem artifact storage for screenshots, traces, step results, mission results, session reports, and evidence
- action loop with guided planning, safe alternative requests, evidence-linked step results, and deterministic replay
- typed Oracle Engine for deterministic network/console/navigation checks and LLM-assisted UI/state checks
- checkpoint-aware Mission Executor with guided-only action limits, mission reports, and trace artifacts
- Bug Investigator with deterministic replay, repeated reproduction, and greedy minimization
- Session Engine with stable-priority mission ordering, per-mission context isolation, and session reports
- FastAPI session API with background execution, status polling, cancellation, and contained artifact retrieval
- Redacted JSON logging for guardrail decisions, evidence creation, and LLM token metrics

### Token efficiency

Durable browser evidence remains full fidelity on disk, while model requests use
compact projections. Planning receives semantic ARIA and element references,
history is reduced to actionable facts, evidence excerpts are bounded, and
deterministic replay results are persisted without polluting guided LLM context.

Screenshots are sent for oracle/anomaly calls by default and omitted from normal
planning. Set `TESTER_OBSERVATION_IMAGE_MODE` to `always` to include planning
images or `off` to disable model-facing images. The saved PNG remains unchanged;
model requests receive a resized JPEG bounded by
`TESTER_OBSERVATION_IMAGE_MAX_SIDE` and `TESTER_OBSERVATION_IMAGE_QUALITY`.

A mission whose explicit checks pass but that also confirms a minor bug is
reported as `passed_with_bugs`, preserving the bug in both the verdict and the
session summary.

### Browser visibility

`TESTER_HEADLESS` controls whether Playwright runs the browser with a visible
window:

- `TESTER_HEADLESS=true` — the browser runs invisibly in background mode. This
  is the default and is best for automated sessions.
- `TESTER_HEADLESS=false` — the browser window opens visibly, so you can watch
  the tester click, fill fields, and navigate.

## Quickstart

```bash
make install
make install-browser
copy .env.example .env  # Windows PowerShell: Copy-Item .env.example .env
```

The Gemini key is optional until the LLM adapter phase. Run the foundation tests with:

```bash
make test
```

The browser integration test uses a local fixture and requires the Chromium runtime installed by `make install-browser`:

```bash
py -3.13 -m pytest tests/unit -v
py -3.13 -m pytest tests/integration -v
```

Start the API with:

```bash
agentic-tester
```

The default endpoints are `GET /health`, `POST /sessions`, `GET /sessions/{id}`,
`GET /sessions/{id}/report`, `GET /sessions/{id}/artifacts/{path}`, and
`POST /sessions/{id}/cancel`.
