<!-- GEB protocol, compact edition (~300 words; for low-context models or tools with rule-size limits). Full version: PROTOCOL_EN.md. -->

# GEB Documentation Protocol (compact)

Code and docs must stay in sync: if one changes and the other doesn't, the task is not done.

**Structure**: L1 root `PROJECT_INDEX.md` (positioning / tree / dependency graph / root file table); L2 one `FOLDER_INDEX.md` per code folder (ledger table: file | responsibility | key exports); L3 four comment lines atop every code file: `[INPUT]` dependencies, `[OUTPUT]` what it provides, `[POS]` role in the system, `[PROTOCOL]` update this header on change, then check the parent index. No headers for generated files / pure configs / vendored deps; no index for folders without code.

**After changing code (mandatory)**: 1) update the changed file's L3 header; 2) reconcile its FOLDER_INDEX.md (add new, clear deleted); 3) update PROJECT_INDEX.md if structure or dependencies changed; 4) run `geb_sync.py` first if available (machine fields auto-rewritten), then `geb_check.py` to verify; 5) end your report with `GEB loop: L3 ✓ | L2 ✓ | L1 —`.

**Before touching anything**: read L1 → target L2 → target L3, then the code. If docs contradict code, fix in passing and say so.

**Initialization**: bottom-up — actually read code to write L3 → summarize into L2 → then L1; never fabricate from filenames.

**Never**: change code without docs; create files without L3 headers; leave ghost index entries after deletions; fabricate docs; defer the loop.

Tools: https://github.com/AaronXinzhian/fugue-docs
