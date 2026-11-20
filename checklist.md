# mem-plane Implementation Checklist

This checklist tracks the work described in `mem-plane-harness-memory-spec.md`. Commit both files to the repository root. The coding agent updates this file as it works, and you use it to check the agent's progress and honesty.

Section numbers like (§5.3) point to sections of the spec.

---

## How to use this file

### Rules for the coding agent

Paste these rules into the agent's instructions (for example, in `CLAUDE.md`):

1. Work on milestones in order. Do not start a milestone until the previous milestone's gate is checked.
2. Tick a task (`[x]`) only when its code is written, its tests pass, and its **Verify** step has been done.
3. After ticking a task, append the short commit hash in the task's `Commit:` field.
4. If a task is partly done, mark it `[~]` and write what is missing in the Notes column of the progress table or in the session log.
5. If a task cannot be done as specified, mark it `[!]`, explain why in the session log, and stop for a human decision. Do not invent a workaround that changes the design.
6. Never delete or weaken an existing test to make the suite pass. If a test is wrong, mark the task `[!]` and explain.
7. Never change the stored tool-text hash constant (§8.2, task M2.13) without writing the reason in the session log.
8. At the end of each working session, add an entry to the session log at the bottom of this file.

### Status markers

| Marker | Meaning |
| :- | :- |
| `[ ]` | Not started |
| `[~]` | In progress or partly done |
| `[x]` | Done and verified |
| `[!]` | Blocked, needs a human decision |

### Checks to run before every gate

These commands must all pass before any milestone gate is ticked:

```bash
pytest -q
mypy --strict src tests
ruff check src tests
ruff format --check src tests
```

---

## Progress summary

The agent updates this table at the end of each session.

| Milestone | Tasks done | Gate passed | Notes |
| :- | :- | :- | :- |
| M0 Fixtures and baseline | 0 / 8 | [ ] | |
| M1 Storage version 1 | 0 / 16 | [ ] | |
| M2 Embeddings, retrieval, tools | 0 / 19 | [ ] | |
| M3 Tool-output shortening | 0 / 10 | [ ] | |
| M4 Transcript archive | 0 / 11 | [ ] | |
| M5 Session context | 0 / 7 | [ ] | |
| M6 Packaging and docs | 0 / 15 | [ ] | |
| M7 Optional features | 0 / 7 | [ ] | |

---

## M0: Fixtures and baseline (§16, §15.2)

Goal: learn the real formats Claude Code uses before building on them.

- [ ] **M0.1** Add a minimal `mem-plane hook <event>` entry point that only saves the raw stdin payload when `MEM_PLANE_HOOK_DEBUG=1`, prints nothing, and always exits 0. (§9.1 rules 2, 4, 7)
  - Files: `src/mem_plane/hooks/runner.py`, `src/mem_plane/cli.py`, `pyproject.toml` (`[project.scripts]`)
  - Verify: `echo '{}' | mem-plane hook stop; echo $?` prints `0` and nothing else.
  - Commit:
- [ ] **M0.2** Register the debug hook for `SessionStart`, `PostToolUse` (Bash), `Stop`, `PreCompact`, `SessionEnd` in a local `.claude/settings.json`.
  - Verify: `/hooks` in Claude Code lists all five.
  - Commit:
- [ ] **M0.3** Run real sessions that include: a small Bash output, a Bash output over 2,000 lines, a failing command, `/compact`, an automatic compaction, `/clear`, and a resume.
  - Verify: payload files exist in `~/.mem-plane/logs/payloads/` for every event type.
  - Commit: (no code)
- [ ] **M0.4** Copy payloads and two transcript files into `tests/fixtures/claude_code/`, scrubbed of personal paths and secrets. Record the Claude Code version in `VERSION`.
  - Verify: `grep -ri "$HOME" tests/fixtures/` returns nothing.
  - Commit:
- [ ] **M0.5** Write `docs/claude-code-formats.md` describing: the exact `tool_response` shape for Bash (every key), the `SessionStart` `source` values seen, the `PreCompact` `trigger` values, transcript entry types, where message content and usage data live, and how a compaction appears in the transcript.
  - Verify: the document names real keys from the fixtures, not guesses.
  - Commit:
- [ ] **M0.6** Write a minimal `mem-plane bench` that reads transcripts and prints requests, input tokens, cache-write tokens, cache-read tokens, output tokens, and compactions. (§15.4)
  - Verify: running it on the M0.3 transcripts prints numbers that look plausible.
  - Commit:
- [ ] **M0.7** Record baseline numbers from M0.6 in `docs/benchmarks.md` under "Baseline (no mem-plane)".
  - Commit:
- [ ] **M0.8** Add a test that runs every fixture payload through the debug runner and asserts exit code 0 and empty stdout.
  - Commit:

**Gate M0:** [ ] Fixtures for every event in §9 are committed, `docs/claude-code-formats.md` exists, baseline numbers are recorded.

**What to check yourself:** open `docs/claude-code-formats.md` and confirm the Bash `tool_response` keys are listed exactly. M3 depends on this.

---

## M1: Storage version 1 (§4, §5)

Goal: a persistent, project-scoped, atomic store.

- [ ] **M1.1** `project.py`: `resolve_project(cwd)` with remote normalization, path fallback, non-git fallback, `MEM_PLANE_PROJECT_ID` override, per-process cache. (§5.2)
  - Verify: `tests/test_project.py` covers SSH, HTTPS, credentials in URL, `.git` suffix, no remote, no git.
  - Commit:
- [ ] **M1.2** `connection.py`: add `busy_timeout=5000`, create `~/.mem-plane/` with `0700` and DB with `0600`, default path `~/.mem-plane/memory.db`, remove `:memory:` default. (§4.2)
  - Verify: `stat -c %a ~/.mem-plane/memory.db` prints `600`.
  - Commit:
- [ ] **M1.3** `schema.py`: all version 1 DDL from §5.3, with the embedding dimension taken from config, not hard-coded.
  - Commit:
- [ ] **M1.4** sqlite-vec version check with fallback tables (no partition key, no metadata column) behind one function. (§5.3)
  - Verify: a test mocks an old `vec_version()` and checks the fallback path.
  - Commit:
- [ ] **M1.5** `meta` table populated with `embedding_model`, `embedding_dimension`, `schema_created_at`; refuse vector writes when the configured model differs. (§5.3)
  - Commit:
- [ ] **M1.6** New Pydantic models in `core/models.py`; `SurrogateHit` kept as a deprecated alias. (§5.4)
  - Commit:
- [ ] **M1.7** `MemoryStore` protocol in `storage/protocols.py`. (§5.5)
  - Commit:
- [ ] **M1.8** Sync `SQLiteStore`: projects, sessions, facts (create, update, delete, pin, get, list), history, chunks, traces, pending embeddings, write vectors.
  - Commit:
- [ ] **M1.9** `create_fact` does dedup by `content_hash`, supersede handling, path hashing, history row, all in one transaction. (§8.2 `record_fact` steps 1, 2, 4, 5)
  - Commit:
- [ ] **M1.10** Every lookup by ID also filters by `project_id`. (§5.5)
  - Verify: a test confirms a fact ID from project A returns nothing for project B.
  - Commit:
- [ ] **M1.11** Async wrapper using `asyncio.to_thread`.
  - Commit:
- [ ] **M1.12** Migration from version 0. (§5.6)
  - Verify: `tests/storage/test_migration_v0.py` builds an old DB with the current DDL and migrates it.
  - Commit:
- [ ] **M1.13** Atomicity test: inject failure between fact insert and FTS insert; nothing is written. (§15.1)
  - Commit:
- [ ] **M1.14** Concurrency test: 8 processes writing to one temp DB with no errors. (§15.1)
  - Commit:
- [ ] **M1.15** Production backend moved behind `production_experimental`, unimplemented methods raise `NotImplementedError`. (§4.3)
  - Commit:
- [ ] **M1.16** Old `SQLiteBackend` class removed or reduced to a deprecated shim with no callers.
  - Commit:

**Gate M1:** [ ] All storage tests in §15.1 pass, and the standard checks pass.

**What to check yourself:** run `sqlite3 ~/.mem-plane/memory.db ".tables"` and compare the list with §5.3.

---

## M2: Embeddings, retrieval, and MCP tools (§6, §7, §8)

Goal: search that actually works, and the new tool set.

- [ ] **M2.1** `FastEmbedClient` with lazy loading, normalization, query prefix, model table. (§6.1)
  - Commit:
- [ ] **M2.2** `embed_query` and `embed_documents` added to the protocol; old methods kept as wrappers. (§6.1)
  - Commit:
- [ ] **M2.3** `build_embedding_client(settings)`: `fastembed`, `none`, `dummy`; dummy refused without `MEM_PLANE_ALLOW_DUMMY_EMBEDDINGS=1`; missing fastembed falls back to keyword only with one warning. (§6.2)
  - Verify: starting the server with `MEM_PLANE_EMBEDDING_PROVIDER=dummy` and no allow flag exits with a clear error.
  - Commit:
- [ ] **M2.4** `embed` and `harness` extras in `pyproject.toml`. (Appendix B)
  - Commit:
- [ ] **M2.5** Background embedder worker in the server. (§6.3)
  - Verify: insert a fact with `embedded_at` NULL, start the server, and within a few seconds `embedded_at` is set.
  - Commit:
- [ ] **M2.6** FTS query builder: sanitize, quote tokens, cap at 24, join with OR. (§7.2)
  - Verify: tests for quotes, parentheses, `*`, empty query, `auth_jwt_v2`, `--no-cov`.
  - Commit:
- [ ] **M2.7** Fact and episode keyword search with `bm25` column weights and project and status filters. (§7.2)
  - Commit:
- [ ] **M2.8** Vector search with partition key, status filter, and the cosine distance cut-off. (§7.3)
  - Commit:
- [ ] **M2.9** `weighted_rrf` for N lists with deterministic ties; four-list fusion with configured weights. (§7.4)
  - Commit:
- [ ] **M2.10** Text output formatter: one line per hit, no scores, no current time, fixed last line. (§7.5)
  - Verify: the same search twice produces byte-identical text.
  - Commit:
- [ ] **M2.11** Staleness check with 10-second hash cache and 5 MB skip. (§7.6)
  - Commit:
- [ ] **M2.12** Static tool text in `server/tool_text.py`. (§8.2)
  - Commit:
- [ ] **M2.13** Tool-text stability test with stored hash constant. (§15.1)
  - Commit:
- [ ] **M2.14** Tools: `search_memory`, `get_memory` (fact, chunk, trace with `grep`, `lines`, default truncation), `record_fact`, `update_memory`, `forget_memory`, `pin_memory`, `memory_status`. (§8.2)
  - Commit:
- [ ] **M2.15** `record_fact` similar-fact hint at cosine similarity 0.92 or above. (§8.2)
  - Commit:
- [ ] **M2.16** Tools never raise; errors return one `error:` line and are logged. (§8.2)
  - Commit:
- [ ] **M2.17** Server startup sequence, project fixed for process lifetime, no `project_id` parameter on any tool. (§8.1)
  - Commit:
- [ ] **M2.18** Old tools removed; `CHANGELOG.md` notes the break. (§8.3)
  - Commit:
- [ ] **M2.19** Retrieval evaluation set and `eval/retrieval/run.py`. (§15.3)
  - Verify: report shows fused recall@5 at least 0.85, fused higher than keyword-only and vector-only, superseded rate 0.
  - Commit:

**Gate M2:** [ ] Search and tool tests pass, and the evaluation report meets its targets. Paste the evaluation numbers into the Notes column.

**What to check yourself:** run `mem-plane search "some decision you recorded"` in a real project and judge whether the results make sense.

---

## M3: Tool-output shortening (§9.2)

Goal: large Bash output stops filling the conversation.

- [ ] **M3.1** Hook input models in `hooks/inputs.py` that ignore unknown fields. (§9.1 rule 1)
  - Commit:
- [ ] **M3.2** Runner safety: catch everything, exit 0, single JSON object or nothing, logging to file only. (§9.1 rules 2 to 4)
  - Commit:
- [ ] **M3.3** Import guard test: `fastembed`, `numpy`, and `mcp` are not imported by the hook path. (§9.1 rule 5)
  - Commit:
- [ ] **M3.4** `preview.py`: banner, head, deduplicated signal lines with line numbers, tail, omission markers, 300-character line cap, size budget with the stated reduction order. (§9.2)
  - Commit:
- [ ] **M3.5** `post_tool_use.py`: thresholds, per-stream "large" rule, exclusion regex, trace saved, replacement built by copying the original object and replacing only `stdout` and `stderr`. (§9.2)
  - Verify: the test asserts every original key is still present in the replacement.
  - Commit:
- [ ] **M3.6** Unknown `tool_response` shape produces no output. (§9.2)
  - Commit:
- [ ] **M3.7** Trace IDs are `t_` plus 10 hex characters; `exit_code` read from whichever key the M0 fixtures show. (§5.3, §9.2)
  - Commit:
- [ ] **M3.8** Trace retention and `mem-plane gc`. (§10, §12)
  - Commit:
- [ ] **M3.9** Hook latency under 150 ms from process start for `post-tool-use` on a small output.
  - Verify: `time (cat tests/fixtures/claude_code/<small-bash>.json | mem-plane hook post-tool-use)`.
  - Commit:
- [ ] **M3.10** Manual check in Claude Code: run a command with large output, confirm the shortened version appears, then ask the model to find a specific line with `get_memory` and `grep`.
  - Commit: (no code)

**Gate M3:** [ ] Hook tests pass on M0 fixtures, and M3.10 worked in a real session.

**What to check yourself:** in the transcript, the large output should appear only as the preview, and the model should not re-run the command to read it.

---

## M4: Transcript archive (§9.3 to §9.5, §9.9, §17.2)

Goal: nothing from a session is ever lost.

- [ ] **M4.1** `redact.py` with every pattern listed in §17.2.
  - Verify: unit tests with fake secrets for each pattern.
  - Commit:
- [ ] **M4.2** Incremental reader with `BEGIN IMMEDIATE`, offset tracking, complete lines only, file-shrink reset. (§9.4)
  - Commit:
- [ ] **M4.3** Normalizer for each entry type in the §9.4 table, key-based and defensive.
  - Commit:
- [ ] **M4.4** Compaction detection using the marker documented in M0.5, with segment increment.
  - Commit:
- [ ] **M4.5** Chunker with target size, entry-boundary cuts, oversized-entry splitting, timestamps, token estimate. (§9.4)
  - Commit:
- [ ] **M4.6** Redaction applied before chunks are stored.
  - Commit:
- [ ] **M4.7** `stop.py` (async). (§9.3)
  - Commit:
- [ ] **M4.8** `pre_compact.py` (synchronous, never blocks, increments compactions). (§9.5)
  - Commit:
- [ ] **M4.9** `session_end.py` (async, best effort, sets `ended_at`). (§9.9)
  - Commit:
- [ ] **M4.10** Completeness script: compares every user and assistant message in a transcript with the stored chunks.
  - Commit:
- [ ] **M4.11** Real session with two compactions; completeness script reports 100 percent and no duplicate chunks.
  - Commit: (no code)

**Gate M4:** [ ] Transcript tests pass and M4.11 succeeded. Paste the completeness script output into the Notes column.

**What to check yourself:** search for a phrase you typed early in a long session with `mem-plane search` and confirm it is found.

---

## M5: Session context (§9.7)

Goal: after compaction, the model knows what memory exists.

- [ ] **M5.1** Section builders: header with counts, pinned, session facts (compact only), recent, tool line. (§9.7)
  - Commit:
- [ ] **M5.2** Deterministic sorting rules exactly as written in §9.7.
  - Commit:
- [ ] **M5.3** Budget enforcement with the stated drop order; summaries cut to 160 characters.
  - Verify: test with 500 facts stays under `MEM_PLANE_SESSION_CONTEXT_MAX_CHARS`.
  - Commit:
- [ ] **M5.4** Empty project prints only the tool line.
  - Commit:
- [ ] **M5.5** Text is factual statements, not commands. (§9.7 note)
  - Commit:
- [ ] **M5.6** Output returned as `additionalContext` JSON; project root and transcript path updated.
  - Commit:
- [ ] **M5.7** Real session: record a decision, run `/compact`, then ask about the decision. The model answers using the listed fact ID.
  - Commit: (no code)

**Gate M5:** [ ] Tests pass and M5.7 worked.

**What to check yourself:** run `/compact` twice in a row with no new memory and confirm the injected block is identical both times.

---

## M6: Packaging and documentation (§10, §11, §13, §14, §15.4)

Goal: a new user can install and use mem-plane from the README alone.

- [ ] **M6.1** CLI: `serve`, `hook`, `status`, `search`, `embed-pending`, `reindex`, `gc`, `export`, `import`. (§10)
  - Commit:
- [ ] **M6.2** `mem-plane doctor` with every check in §10 and `--print-hooks`.
  - Commit:
- [ ] **M6.3** `plugin/` directory: manifest checked against current Claude Code plugin docs, `.mcp.json`, `hooks/hooks.json`, skill. (§14.2)
  - Commit:
- [ ] **M6.4** CI check that the plugin version equals the package version. (§14.2)
  - Commit:
- [ ] **M6.5** Single skill file under 70 lines covering all seven points in §11; old four skills removed; Antigravity mirror updated.
  - Commit:
- [ ] **M6.6** Governor moved to `sdk/prompt_zones.py`; pruner wrapper in `sdk/pruner.py`; old imports removed from `core/__init__.py`. (§13)
  - Commit:
- [ ] **M6.7** All settings in §12 implemented in `config.py`.
  - Commit:
- [ ] **M6.8** Full `mem-plane bench` with cost-weighted totals and pruning statistics. (§15.4)
  - Commit:
- [ ] **M6.9** Benchmark fixture repository and 5 task scripts. (§15.4)
  - Commit:
- [ ] **M6.10** Benchmark runs: 5 tasks, 5 runs each, with and without mem-plane, plus the recall quiz. Results in `docs/benchmarks.md`.
  - Commit:
- [ ] **M6.11** README rewritten: install steps, how it works, real benchmark numbers only, unproven claims removed. (§13, §14.1)
  - Commit:
- [ ] **M6.12** `docs/storage.md` explaining the SQLite decision and the frozen backend. (§4)
  - Commit:
- [ ] **M6.13** `docs/architecture.md` updated to the hook-based design.
  - Commit:
- [ ] **M6.14** `CHANGELOG.md` complete for the release.
  - Commit:
- [ ] **M6.15** Clean-machine test: fresh user account or container, follow the README only, `mem-plane doctor` reports no problems.
  - Commit: (no code)

**Gate M6:** [ ] M6.15 succeeded and `docs/benchmarks.md` has real numbers.

**What to check yourself:** compare the baseline and mem-plane rows in `docs/benchmarks.md`. Weighted input tokens and compactions per run should both be lower, and task success should not be lower.

---

## M7: Optional features (§9.6, §9.8, §13)

- [ ] **M7.1** Auto recall hook, keyword only, off by default. (§9.8)
  - Commit:
- [ ] **M7.2** `AUTO_RECALL_MIN_SCORE` tuned with the evaluation set; result recorded in `docs/benchmarks.md`.
  - Commit:
- [ ] **M7.3** Fact extractor, off by default, writes with `source='extractor'`, never pins. (§9.6)
  - Commit:
- [ ] **M7.4** Extractor prompt and output parsing tests with a mocked model.
  - Commit:
- [ ] **M7.5** `examples/custom_loop.py` using pinned facts in a cached system prompt. (§13)
  - Commit:
- [ ] **M7.6** Documentation for all three features, each stating its token or API cost.
  - Commit:
- [ ] **M7.7** Benchmark rerun with auto recall on, compared with off.
  - Commit:

**Gate M7:** [ ] All three features are off by default, documented, and tested.

---

## Red flags to watch for

If you see any of these in the agent's commits or messages, stop it and ask why.

- A test was deleted, skipped, or had its assertion loosened in the same commit as a code change.
- The tool-text hash constant changed with no session log entry.
- A task was ticked with no commit hash.
- A hook can print anything other than one JSON object, or can exit with a code other than 0.
- Hook code imports `fastembed`, `numpy`, or `mcp`.
- A tool accepts `project_id` from the model.
- Tool output contains scores, indented JSON, or the current time.
- The `PostToolUse` replacement is built from scratch instead of by copying the original object.
- `Read`, `Edit`, or `Write` output is being shortened.
- A design decision from the spec was changed without a `[!]` marker and a log entry.
- README numbers that do not appear in `docs/benchmarks.md`.

---

## Decisions and deviations log

Record every decision that differs from the spec, or fills a gap the spec left open.

| Date | Task | Decision | Reason | Approved by |
| :- | :- | :- | :- | :- |
| | | | | |

---

## Session log

The agent adds one entry per working session, newest first.

```
### YYYY-MM-DD, session N
Tasks completed: M1.1, M1.2
Tasks in progress: M1.3 (DDL written, dimension still hard-coded)
Blocked: none
Test status: 52 passed, 0 failed; mypy clean; ruff clean
Notes:
```
