# Protocols

This directory will hold the protocol contracts between:

- harness supervisor and specialist agents
- harness and `Center`
- harness and remote `Client` execution surfaces
- harness and Codex App Server based AI worker sessions

The protocol baseline is intentionally separate from product code so the harness can evolve without hiding its control contracts inside unrelated runtime modules.
