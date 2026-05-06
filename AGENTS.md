# AGENTS.md

This repository keeps the canonical agent instructions in `CLAUDE.md`.

When working in this repository, Codex must read `CLAUDE.md` first and follow it as the single source of truth for project context, commands, architecture notes, deployment notes, and operational constraints.

## Memory

Project memory is stored under `.claude/memory/`.

At the start of a new conversation, read `.claude/memory/MEMORY.md`, then read the indexed memory files as needed to restore project context.

## Maintenance Rule

Do not duplicate the contents of `CLAUDE.md` here. Update `CLAUDE.md` directly when project instructions change, so Claude and Codex consume the same configuration.
