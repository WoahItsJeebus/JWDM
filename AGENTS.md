# AGENTS.md

## JWDM working agreements

- Read `PROJECT_CONTEXT.md` before planning or modifying this repository.
- Treat its fixed decisions and safety invariants as binding unless the owner explicitly changes them.
- Work in small, reviewable phases. Do not implement deferred features early.
- The current first target is Phase 0 only unless the user requests another phase.
- Keep business logic outside UI widgets.
- Use typed Python, structured logging, explicit errors, and testable services.
- Never silently overwrite, delete, execute, or upload user files.
- Do not move a candidate before its readiness stage passes.
- Preserve undoability and transaction safety in filesystem work.
- `.\Build.ps1` is the canonical interactive build command and must produce and launch a compiled test executable.
- Run relevant tests and the compiled build before calling a task complete.
- Update `PROJECT_CONTEXT.md` when a durable product or architecture decision changes.
- Report files changed, commands run, test/build results, and unresolved risks.
