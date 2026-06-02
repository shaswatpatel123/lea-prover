# Lea docs

Project docs and learnings kept alongside the code. This folder is where design
rationale, architecture decisions, and notes-for-later live, so the *why* behind
the code survives beyond any single change.

## Index

- [decisions.md](decisions.md) — architecture decision log: the config-driven
  direction, agent-as-product / eval-as-consumer split, the streaming LiteLLM
  engine, `model_kwargs`, and cost transparency — with rationale.

## Conventions

- One concern per file; link between files rather than duplicating.
- Decisions are append-mostly: when one is reversed, add a new dated entry that
  supersedes it rather than silently editing history.
