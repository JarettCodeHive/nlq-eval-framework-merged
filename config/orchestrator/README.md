# Orchestrator Config

Configuration contract drafts for the deferred Evaluation Orchestrator
(scope doc §12.1). See [`config.contract.yaml`](config.contract.yaml) for
the draft — generalizes `judge/pulse_client.py`'s Pulse-specific settings
(endpoint, auth, request/response shape, timeouts, concurrency) into a
platform-agnostic shape. Not consumed by any code; see
[`../../orchestrator/ARCHITECTURE.md`](../../orchestrator/ARCHITECTURE.md)
for how it fits the rest of the design.

This folder is for design/config documentation only until orchestrator
implementation is formally in scope.
