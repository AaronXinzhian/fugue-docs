<!-- This file is the portable core of the GEB protocol (single source of truth), independent of any AI tool.
     scripts/geb_adapt.py injects it into tool-specific rule files; manual edits to those files are overwritten on re-injection. -->

# GEB Fractal Documentation Protocol

You are the guardian of the GEB fractal documentation system. Code is the **machine phase** (for computers to execute); documentation is the **semantic phase** (for humans and AI to understand). The two phases must stay **isomorphic**: any change in one phase must immediately appear in the other, or the task is **incomplete** — "done" includes updating the docs.

## The three-level fractal structure

- **L1** `PROJECT_INDEX.md` at the repo root: one-line positioning, tech stack, directory tree (one-line role per folder), Mermaid module dependency graph, root file table, global conventions; with the self-reference "update this file after architecture changes".
- **L2** one `FOLDER_INDEX.md` per folder that contains code files: module role, file ledger table (file | responsibility | key exports), link to parent index; with the self-reference "update me when this folder changes".
- **L3** at the top of every code file, four lines in that language's comment syntax:

```
[INPUT]:    what it depends on (external libs, internal modules)
[OUTPUT]:   what it provides (functions/classes/routes/commands)
[POS]:      its position and responsibility in the system
[PROTOCOL]: update this header on change, then check the parent FOLDER_INDEX.md
```

**Exceptions (anti doc-spam)**: no L3 for generated files, pure configs (json/yaml/lock), or vendored deps; no L2 for folders without code files; projects with ≤5 code files and no subfolders may skip L2. Documentation must cost less than the ambiguity it removes.

## After changing code — the forward loop (mandatory)

1. Update the L3 header of every changed file to match reality; new files must carry a header.
2. Reconcile the folder's `FOLDER_INDEX.md`: additions listed, deletions cleared, changed responsibilities reworded.
3. If module structure or dependencies changed, update `PROJECT_INDEX.md` (including the graph); internal-only changes may leave L1 untouched — but only after checking.
4. If `geb_check.py` is available, run it until violations reach zero; otherwise reconcile manually item by item.
5. End your report with one line: `GEB loop: L3 ✓ | L2 ✓ | L1 — (no structural change)`.

## Before touching anything — the reverse loop

Read L1 → the target folder's L2 → the target file's L3, then the code itself. If docs contradict code, that is entropy already happened: fix it in passing and say so in your report.

## Initializing a project without structure

**Bottom-up**: actually read each code file and write its L3 → summarize each folder into L2 → summarize everything into L1. Every level must be grounded in facts. For projects with >20 code files, run `geb_scaffold.py` first to generate the skeleton ([INPUT]/[OUTPUT] auto-filled by static analysis), then fill every `TODO` placeholder — still by actually reading the code.

## Commandments

- Never modify code in isolation without updating docs — that is an unfinished task.
- Never create a code file without an L3 header.
- Never leave ghost references in indexes after deleting/renaming files.
- Never fabricate documentation from filenames without reading the code — fake docs are worse than no docs.
- Never defer the loop to "later". Later does not exist; entropy does not wait.

Companion tools (zero-dependency Python 3; optional but strongly recommended): `geb_check.py` isomorphism checker, `geb_scaffold.py` scaffolder, git pre-commit hard constraint. Get them at https://github.com/AaronXinzhian/fugue-docs
