---
title: Fix skill upload 200-file limit + packaging hygiene (public repo)
type: fix
status: active
date: 2026-04-14
deepened: 2026-04-14
---

# Fix skill upload 200-file limit + packaging hygiene (public repo)

## Overview

claude.ai's "Upload skill" UI rejects zips with more than 200 files. Zipping the public `mvanhorn/last30days-skill` repo produces 406 files, so the upload fails outright (evidence: Trevin's 2026-04-14 report). Root cause is an accidentally committed npm package under `vendor/` (215 files of dead weight from PR #48) plus the absence of a user-facing packaging path that matches Anthropic's canonical `.skill` format.

Goal: let any user produce a compliant `last30days.skill` file in one command, matching Anthropic's skill-creator packaging contract, while also removing genuine dead weight from the repo (unused vendor, legacy plans).

## Problem Frame

- Trevin tried to upload the public repo as a Claude Skill and hit the 200-file limit
- 215 of 406 files are `vendor/package/` - an extracted `steipete-bird-0.8.0.tgz` that no code imports
- The real runtime X client lives at `scripts/lib/vendor/bird-search/` (15 files, referenced by `scripts/lib/bird_x.py:5` and `tests/test_bird_x.py:133`)
- `.clawhubignore` is ClawHub-specific and does not affect a hand-rolled zip upload
- Users have no documented path to produce a compliant upload zip
- Legacy top-level `plans/` folder holds pre-`docs/plans/` planning artifacts (confirmed waste by Matt, 2026-04-14 chat)

## Requirements Trace

- R1. After this plan lands, the produced upload zip is =200 files
- R2. The X/bird-search runtime still works - no regression in `tests/test_bird_x.py`
- R3. A contributor following README instructions can produce a Claude-Skill-upload-compatible `.skill` file in one command
- R4. Re-introduction of a root `vendor/` directory is prevented via `.gitignore`
- R5. No runtime behavior changes for existing skill consumers (Claude Code plugin, ClawHub, Gemini)
- R6. Produced zip matches Anthropic's canonical skill-folder layout: top-level directory named exactly `last30days` containing `SKILL.md` at its root, with YAML frontmatter `name: last30days`
- R7. Root `SKILL.md` frontmatter passes Anthropic's documented limits: `name` =64 chars (currently 10), `description` =200 chars (currently 228, needs trimming)
- R8. Produced zip contains exactly one `SKILL.md` (at `last30days/SKILL.md`) - no conflicting second skill spec, no symlinks that the uploader may reject or break
- R9. No runtime import reaches an excluded path (proven by import-graph audit, not just asserted)

## Scope Boundaries

Non-goals:
- Not touching the private repo or ClawHub publish flow (those have their own strip script)
- Not resolving the adjacent open issues (#239 plugin loader path-escape, #236 OpenClaw paths, #231 security scan, #190 version drift, #184 Gemini install) - each deserves its own plan
- Not redesigning the skill into self-contained subfolders or splitting scripts into a separate package
- Not adding CI enforcement of the 200-file cap (possible follow-up)

## Context and Research

### Anthropic's canonical skill-upload contract

Sourced from Anthropic's skill-creator repo (`anthropics/skills/skills/skill-creator/scripts/package_skill.py`) and help-center docs:

1. **Output format:** a `.skill` file, which is a standard zip with the `.skill` extension.
2. **Top-level entry in the zip must be a single directory** whose name matches `name:` in the skill's YAML frontmatter. Anthropic's packager uses `arcname = file_path.relative_to(skill_path.parent)`, so the zip always contains `<skill_name>/...`.
3. **That directory must contain `SKILL.md`** at its root (the packager explicitly validates this).
4. **Required YAML frontmatter:** `name` (=64 chars, lowercase + hyphens) and `description` (=200 chars). Our root SKILL.md already satisfies both.
5. **Canonical exclusions** applied by Anthropic's packager:
   - Directories: `__pycache__`, `node_modules`
   - Root-only: `evals/`
   - File globs: `*.pyc`
   - Files: `.DS_Store`
6. **Empirical limit:** the upload UI rejects =200 files (screenshot 2026-04-14). Not documented, but confirmed.
7. **Per-file size cap** is not publicly documented; general claude.ai uploads cap at 30MB per file. Conservative target: keep any single file under 10MB.

### Relevant code and patterns in this repo

- `SKILL.md` (root, 1382 lines, 80KB) - `name: last30days`, `user-invocable: true`. This is the skill.
- `skills/last30days/SKILL.md` (230 lines) - `name: last30days-v3-spec`, `user-invocable: false`. Internal architecture spec, separate skill name - not the upload target.
- `vendor/package/` - accidental commit from PR #48, 215 files, zero importers.
- `vendor/steipete-bird-0.8.0.tgz` - source tarball, also unused at runtime.
- `scripts/lib/vendor/bird-search/` - the ACTUAL vendored bird-search client (15 files). Keep.
- `plans/` (top-level, 2 files: `feat-add-websearch-source.md`, `fix-strict-date-filtering.md`) - legacy, pre-`docs/plans/` convention. Matt confirmed delete.
- `scripts/sync.sh` - deploys skill to `~/.claude`, `~/.agents`, `~/.codex`. Reference for runtime-required files.
- `.clawhubignore` - existing exclude list for the ClawHub path. Not used here, but good cross-reference for what is runtime-irrelevant.
- `.gitignore` - current dev excludes (`.venv/`, `__pycache__/`, `.DS_Store`, etc).

### Institutional learnings

- Private repo has `scripts/clawhub-publish.sh` + `scripts/strip_for_openclaw.py` that build a staging dir with only OpenClaw-safe files. Not needed for this public-path upload; `git archive` with `--prefix` is sufficient and dependency-free.
- PR #48 introduced `vendor/package/` unintentionally. No code imports from it.

### File count math (verified via dry run)

| Strategy | File count | Under cap? |
|---|---|---|
| Current repo, zip as-is | 406 | No |
| After `vendor/` deleted | 191 | Yes (thin margin) |
| After `vendor/` + `plans/` deleted, no further excludes | 189 | Yes |
| With full planned excludes (Anthropic canonical + tests/docs/fixtures/assets/dev manifests/nested skill dirs) | 81 | Comfortable headroom |

Dry run run on 2026-04-14 against the current working tree. Simulated the proposed `.gitattributes` with a `find` filter matching the intended exclude list. Result: 81 files, 868KB uncompressed. Actual `git archive` output may differ slightly (by 1-2 files) but will land well under 200.

### Runtime import audit (proves core experience unchanged)

Grepped all `import`/`from` statements in `scripts/**/*.py`. Non-stdlib imports resolve to only:
- `lib.*` (internal package at `scripts/lib/`)
- `store` (internal module at `scripts/store.py`)
- `scripts.*` (internal)

No runtime import reaches `tests/`, `docs/`, `fixtures/`, `vendor/` (root), `plans/`, `assets/`, `.agents/`, `.codex-plugin/`, `.hermes-plugin/`, or any other excluded path. The shipped `.skill` file contains everything the runtime needs and nothing it does not.

### Symlink and multi-SKILL.md audit

The repo contains one symlink: `skills/last30days-nux/SKILL.md -> ../../SKILL.md`. Three SKILL.md files in total:
- `SKILL.md` (root, `name: last30days`, `user-invocable: true`) - the actual skill
- `skills/last30days/SKILL.md` (`name: last30days-v3-spec`, `user-invocable: false`) - internal architecture doc
- `skills/last30days-nux/SKILL.md` (symlink to root) - nux variant reference

Shipping all three inside one zip creates two rejection risks:
1. Uploader sees multiple `SKILL.md` with conflicting `name:` values and refuses or misbinds
2. `git archive` stores the symlink as a symlink entry; the uploader may reject symlinked entries on principle

Both risks disappear by excluding `skills/` entirely from the zip. The two internal skill definitions are not needed for claude.ai skill execution - they serve the repo as documentation / Claude Code plugin layout, not the direct upload path.

### Sources consulted

- Anthropic skill help center article (general upload guidance, no file-count number documented)
- [anthropics/skills README](https://github.com/anthropics/skills/blob/main/README.md) - YAML frontmatter requirements
- [anthropics/skills package_skill.py](https://github.com/anthropics/skills/blob/main/skills/skill-creator/scripts/package_skill.py) - canonical exclusions and arcname shape
- Trevin's 2026-04-14 chat screenshot (empirical 200-file cap)
- Adjacent issues #239, #236, #190 for context on current packaging mess

## Key Technical Decisions

- **Delete `vendor/` outright** rather than gitignore-and-leave. Pure dead weight. Rationale: the real vendored client is at `scripts/lib/vendor/bird-search/`, root `vendor/` has zero importers; keeping it invites re-upload.
- **Delete top-level `plans/`** (Matt confirmed). Rationale: superseded by `docs/plans/`. Moving content into `docs/plans/` if any is still relevant; otherwise just delete.
- **Produce a `.skill` file (not a plain `.zip`)** via `git archive --format=zip --prefix=last30days/ -o dist/last30days.skill HEAD`. Rationale: matches Anthropic's canonical contract - zip extension is cosmetic, but the `.skill` affordance is what the upload UI expects.
- **Use `git archive` + `.gitattributes export-ignore`** rather than a Python packager. Rationale: no Python dependency at build time, honors git's declarative exclude model, reusable by anyone running `git archive` directly.
- **Mirror Anthropic's canonical exclusions in `.gitattributes`** (`__pycache__`, `node_modules`, `*.pyc`, `.DS_Store`, `evals/`) alongside our repo-specific excludes. Rationale: future-proof if a contributor adds node deps; keeps us aligned with the Anthropic baseline.
- **Exclude `skills/` from the upload zip** (covers `skills/last30days/SKILL.md` and `skills/last30days-nux/SKILL.md`). Rationale: shipping multiple SKILL.md files with different `name:` values is a likely uploader-rejection cause, and the symlink at `skills/last30days-nux/SKILL.md` is an independent rejection risk. Repo contents stay intact - Claude Code plugin and GitHub viewers still see the directory.
- **Keep `.clawhubignore` as-is** - it serves the ClawHub publish path separately. Do not merge the two lists; different consumers, different exclusions.
- **Prevent regression with a `/vendor/` entry in `.gitignore`** (leading slash, so `scripts/lib/vendor/` is unaffected).
- **Do not address #239 `"skills": ["./"]` path-escape here.** That is a plugin.json change, not a zip-packaging change. Separate plan.

## Open Questions

### Resolved during planning

- Is root `vendor/` used? No. Grep for `vendor/package`, `vendor/steipete`, `from vendor` returns zero hits outside `scripts/lib/vendor/`.
- Is `scripts/lib/vendor/bird-search/` safe? Yes. Referenced by `scripts/lib/bird_x.py:5` and `tests/test_bird_x.py:133`.
- What name does the top-level zip directory need? `last30days` - matches `name: last30days` in the root `SKILL.md` frontmatter.
- Does `skills/last30days/SKILL.md` conflict? No. It declares a different skill name (`last30days-v3-spec`) and is `user-invocable: false`. Not the upload target, and safe to ship inside the zip.
- Is there a documented file-count cap? No. 200 is empirical from the UI error screenshot.
- Should we gate this on a version bump? Yes, 3.0.0 - 3.0.1. Same API, same runtime, smaller and uploadable package.

### Deferred to implementation

- Exact `.gitattributes` export-ignore entries may need one tuning pass if `git archive` surfaces a file we forgot. Verification step catches it.
- Whether to delete `SKILL-original.md` from the repo entirely or just export-ignore. Leaning export-ignore to preserve git history context.
- Whether any content in `plans/*.md` is still live reference material. If so, move to `docs/plans/` under new naming convention; if not, delete outright.

## Implementation Units

- [ ] **Unit 1: Remove accidental `vendor/` commit**

**Goal:** Delete the root `vendor/` directory and the stray `.tgz`, both unused at runtime.

**Requirements:** R1, R2, R5

**Dependencies:** None

**Files:**
- Delete: `vendor/` (entire tree, 215 files)
- Delete: `vendor/steipete-bird-0.8.0.tgz`
- Modify: `.gitignore` (add `/vendor/` to prevent regression - leading slash to avoid matching `scripts/lib/vendor/`)

**Approach:**
- Single commit: `chore: remove unused root vendor/ directory (215 files from PR #48)`
- Verify `scripts/lib/vendor/bird-search/` is untouched
- Verify no `from vendor` or `vendor/package` references appear in the diff

**Patterns to follow:**
- Commit message style matches recent history

**Test scenarios:**
- Happy path: `find . -type f -not -path './.git/*' | wc -l` returns =200 after commit
- Integration: `python -m pytest tests/test_bird_x.py -q` passes - confirms the real vendored client still resolves
- Integration: `bash scripts/sync.sh` completes without error

**Verification:**
- Zero files remain under `vendor/` on `main`
- `tests/test_bird_x.py` still passes
- `.gitignore` now contains `/vendor/`

- [ ] **Unit 2: Remove legacy top-level `plans/` directory**

**Goal:** Delete the pre-`docs/plans/` folder (Matt confirmed waste).

**Requirements:** R1, R5

**Dependencies:** None (independent of Unit 1)

**Files:**
- Delete: `plans/feat-add-websearch-source.md`
- Delete: `plans/fix-strict-date-filtering.md`
- Delete: `plans/` (now empty)

**Approach:**
- Skim both files first. If either still reflects real upcoming work, port it to `docs/plans/YYYY-MM-DD-NNN-<type>-*-plan.md` before deletion. If not, delete.
- Commit: `chore: remove legacy plans/ directory (superseded by docs/plans/)`

**Test scenarios:**
- Test expectation: none - pure housekeeping, no code paths affected

**Verification:**
- `plans/` does not exist on `main`
- Nothing in the repo references `plans/feat-add-websearch-source.md` or `plans/fix-strict-date-filtering.md` (grep to confirm)

- [ ] **Unit 3: Declare zip-time excludes via `.gitattributes`**

**Goal:** Use `export-ignore` so `git archive` produces a skill-shaped zip without hand-filtering.

**Requirements:** R1, R3, R6

**Dependencies:** Unit 1, Unit 2

**Files:**
- Create: `.gitattributes`

**Approach:**
- Anthropic canonical exclusions (match `package_skill.py`):
  - `__pycache__/` export-ignore
  - `node_modules/` export-ignore
  - `*.pyc` export-ignore
  - `.DS_Store` export-ignore
  - `evals/` export-ignore
- Repo-specific exclusions (dev/docs/build artifacts not needed at runtime):
  - `tests/` (64 files)
  - `docs/` (17 files including `docs/test-results/`)
  - `fixtures/` (7 files)
  - `assets/` (5 files, 14MB of README media)
  - `SKILL-original.md` (historical)
  - `SPEC.md`, `TASKS.md`, `test-run.log`, `CONTRIBUTORS.md`, `HERMES_SETUP.md`, `release-notes.md`, `CHANGELOG.md`
  - `uv.lock`
  - `.agents/`, `.codex-plugin/`, `.hermes-plugin/`, `.claude-plugin/` (platform adapters - skill-upload path is platform-agnostic)
  - `.clawhubignore`, `.gitignore`, `.gitattributes`
  - `skills/` (avoid second SKILL.md with conflicting `name:`; also drops the symlink at `skills/last30days-nux/SKILL.md`)
- Keep in archive: `scripts/` (runtime), root `SKILL.md`, `README.md`, `LICENSE`, `pyproject.toml`, `CLAUDE.md`, `gemini-extension.json`, `agents/`, `hooks/`

**Technical design:** *(directional guidance, not implementation spec)*

```gitattributes
# Anthropic canonical skill-packaging excludes
__pycache__/ export-ignore
node_modules/ export-ignore
*.pyc export-ignore
.DS_Store export-ignore
evals/ export-ignore

# Repo-specific: tests + docs + media (not runtime)
tests/ export-ignore
docs/ export-ignore
fixtures/ export-ignore
assets/ export-ignore

# Repo-specific: historical + dev manifests
SKILL-original.md export-ignore
SPEC.md export-ignore
...
```

**Patterns to follow:**
- `.gitattributes` export-ignore syntax per [git docs](https://git-scm.com/docs/gitattributes#_creating_an_archive)

**Test scenarios:**
- Happy path: `git archive --format=zip HEAD | zipinfo -1 - | wc -l` returns =200
- Happy path: zip contains `SKILL.md`, `scripts/last30days.py`, `scripts/lib/bird_x.py`, `scripts/lib/vendor/bird-search/lib/cookies.js`
- Happy path: zip contains exactly one `SKILL.md` entry at the top level (not multiple, not a symlink)
- Edge case: zip does NOT contain `tests/`, `docs/`, `assets/*.jpeg`, `*.mp3`, `skills/`
- Edge case: no symlink entries in the zip (`unzip -l` lines starting with `l`)
- Edge case: zip size stays under ~2MB (if over 5MB an unintended large file slipped through)

**Verification:**
- Running `git archive --format=zip --output=/tmp/test.zip HEAD && unzip -l /tmp/test.zip | tail -1` reports =200 files and a sane byte count

- [ ] **Unit 4: Add `scripts/build-skill.sh` user-facing builder**

**Goal:** One-command path to produce a Claude-upload-compatible `.skill` file.

**Requirements:** R3, R6

**Dependencies:** Unit 3

**Files:**
- Create: `scripts/build-skill.sh`
- Modify: `.gitignore` (add `/dist/` for build artifact)

**Approach:**
- Bash, executable, `set -euo pipefail`
- `git archive --format=zip --prefix=last30days/ --output=dist/last30days.skill HEAD`
- The `--prefix=last30days/` nests everything under `last30days/` inside the zip, matching Anthropic's arcname contract
- Refuse to build if working tree is dirty (`git diff --quiet && git diff --cached --quiet`)
- Print file count, archive size, and path to paste into the upload UI
- Fail with a clear error if count exceeds 200 (defensive check)

**Technical design:** *(directional guidance, not implementation spec)*

```bash
#!/usr/bin/env bash
# build-skill.sh - package repo as a Claude-upload-ready .skill file
# Usage: bash scripts/build-skill.sh
set -euo pipefail

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "error: working tree is dirty - commit or stash first" >&2; exit 1
fi

mkdir -p dist
out="dist/last30days.skill"
git archive --format=zip --prefix=last30days/ --output="$out" HEAD

count=$(unzip -l "$out" | tail -1 | awk '{print $2}')
[ "$count" -le 200 ] || { echo "error: $count files in zip, cap is 200" >&2; exit 1; }
echo "built $out ($count files, $(du -h "$out" | cut -f1))"
```

**Patterns to follow:**
- Style of `scripts/sync.sh` (bash, top-of-file comment, `set -euo pipefail`)

**Test scenarios:**
- Happy path: clean tree, `bash scripts/build-skill.sh` produces `dist/last30days.skill` with =200 files and the top-level entry is `last30days/`
- Happy path: `unzip -p dist/last30days.skill last30days/SKILL.md | head -2` shows `---` (frontmatter start) confirming SKILL.md is at the right location
- Edge case: dirty working tree - script exits non-zero with clear error
- Edge case: idempotent - running twice overwrites cleanly
- Error path: if a future change inflates file count past 200, the defensive `[ "$count" -le 200 ]` check fails and the script refuses to produce a broken output

**Verification:**
- `bash scripts/build-skill.sh && unzip -l dist/last30days.skill | grep "^    0  .* last30days/$"` confirms the prefix directory exists
- `unzip -l dist/last30days.skill | grep "last30days/SKILL.md"` confirms SKILL.md is at the expected path
- `unzip -l dist/last30days.skill | grep -c "SKILL.md"` returns exactly 1
- `unzip -l dist/last30days.skill | awk '{print $NF}' | grep -v "^$" | sort -u | grep "skills/" || true` returns nothing (confirms internal skill dirs excluded)
- Gate: a contributor must run `bash scripts/build-skill.sh` on their branch and attach the produced file to their PR before merging any change that touches `.gitattributes` or exclude-sensitive paths

- [ ] **Unit 5: Document the upload path in README**

**Goal:** Users know how to produce an upload `.skill` without reading the source.

**Requirements:** R3

**Dependencies:** Unit 4

**Files:**
- Modify: `README.md` (add a short "Upload as a Claude Skill" subsection under the existing install section)

**Approach:**
- One paragraph plus a single command block: `bash scripts/build-skill.sh`
- Mention the 200-file cap as context so future changes do not bust it
- Point users at the claude.ai skill upload UI (note: link only if a stable URL exists at implementation time, otherwise describe the UI path)

**Test scenarios:**
- Test expectation: none - pure documentation change

**Verification:**
- `grep -n "build-skill" README.md` returns a hit
- Instructions match actual script behavior

- [ ] **Unit 6: Trim SKILL.md description to =200 chars**

**Goal:** Make root `SKILL.md` frontmatter pass Anthropic's documented `description` limit.

**Requirements:** R7

**Dependencies:** None (independent of other units)

**Files:**
- Modify: `SKILL.md` (frontmatter `description:` field only)

**Approach:**
- Current description is 228 chars. Cut 28+ chars without losing signal.
- Suggested rewrite (196 chars): `"Multi-query social search with planned queries. Research any topic across Reddit, X, YouTube, TikTok, Instagram, Hacker News, Polymarket, and the web. Gemini/OpenAI fallback when needed."`
- Confirm the trimmed version still surfaces for the right prompts (smoke test: run `python scripts/last30days.py "test" --emit=compact` and confirm behavior unchanged; description is metadata, not runtime input)
- Update `skills/last30days/SKILL.md` description too if it exceeds 200 chars (check during implementation)

**Test scenarios:**
- Happy path: `python3 -c "import re; d=open('SKILL.md').read(); m=re.search(r'^description:\s*\"(.+?)\"', d, re.M); assert len(m.group(1)) <= 200, len(m.group(1))"` passes

**Verification:**
- Description field is =200 chars in root SKILL.md
- Skill still triggers on relevant prompts (manual smoke check)

- [ ] **Unit 7: Version bump and changelog**

**Goal:** Ship as 3.0.1 so consumers see the packaging fix.

**Requirements:** R5

**Dependencies:** Units 1-6

**Files:**
- Modify: `.claude-plugin/plugin.json` (3.0.0 - 3.0.1)
- Modify: `SKILL.md` frontmatter version
- Modify: `skills/last30days/SKILL.md` frontmatter version
- Modify: `gemini-extension.json` version (note: #190 flags this as stale at 2.9.5; bumping here partially addresses that but full resolution is out of scope)
- Modify: `CHANGELOG.md`
- Modify: `release-notes.md`

**Approach:**
- Atomic version bump across all manifests
- Changelog entry: "Packaging: `scripts/build-skill.sh` produces a compliant `.skill` file; removed unused root `vendor/` (215 files) and legacy `plans/`; repo file count fits under claude.ai's 200-file upload cap"

**Test scenarios:**
- Happy path: `grep -rn "3.0.1" SKILL.md skills/last30days/SKILL.md .claude-plugin/plugin.json gemini-extension.json` returns four consistent hits
- Integration: `bash scripts/sync.sh` completes cleanly

**Verification:**
- All four version declarations read `3.0.1`
- CHANGELOG and release-notes have dated entries

## System-Wide Impact

- **Interaction graph:** Skill-runtime import graph is unchanged. Removed code (root `vendor/`, `plans/`) has zero importers.
- **Error propagation:** `build-skill.sh` is a new surface; failure mode is non-zero exit with clear stderr. No runtime error paths touched.
- **State lifecycle risks:** None. `dist/` is gitignored build output.
- **API surface parity:** No change to any user-facing API, CLI flag, config key, or SKILL.md contract.
- **Integration coverage:** `tests/test_bird_x.py` exercises the real vendored client - if it regressed, the test fails. Run it after Unit 1.
- **Unchanged invariants:** `scripts/lib/vendor/bird-search/` stays. `scripts/sync.sh` deploy behavior unchanged. ClawHub publish flow (private repo) untouched. Claude Code plugin install via GitHub URL still works.

## Risks and Dependencies

| Risk | Mitigation |
|------|------------|
| Deleting `vendor/` silently breaks something we missed | Run `pytest tests/test_bird_x.py` and `bash scripts/sync.sh` after the delete; grep for `vendor/package` before merging |
| claude.ai rejects the `.skill` file for a reason other than file count (e.g., frontmatter character, hidden file) | Test-upload the produced artifact against claude.ai once before merging; iterate on `.gitattributes` if needed |
| `.gitattributes` over-excludes and breaks the runtime skill | Unit 3 verification step explicitly checks runtime paths are present in the produced archive |
| A future PR re-vendors something at `/vendor/` and busts the 200 cap again | `/vendor/` in `.gitignore` plus the defensive `=200` check in `build-skill.sh` catches it |
| Version bump collides with in-flight PRs that also bump versions | Coordinate with #229, #217 which touched version strings; check before merging |
| `skills/last30days/SKILL.md` (internal spec) being shipped inside the zip confuses the claude.ai uploader | Resolved by excluding `skills/` from the zip (Unit 3). Internal spec remains in the repo for plugin consumers |
| `skills/last30days-nux/SKILL.md` is a symlink to `../../SKILL.md`; claude.ai may reject zips with symlink entries | Resolved by excluding `skills/` from the zip (Unit 3). Symlink never enters the archive |

## Documentation and Operational Notes

- Update README only (Unit 5). No runbook, no migration, no flag.
- No deployment step - plugin consumers get the packaging fix automatically on next update.
- Release notes flag: manual uploaders should re-zip via `scripts/build-skill.sh`.
- Opportunistic future work (out of scope here): CI check that fails PRs that push the zip over 200 files.

## Sources and References

- Trevin's 2026-04-14 chat screenshot: "Zip contains too many files (maximum 200)"
- [anthropics/skills README](https://github.com/anthropics/skills/blob/main/README.md) - YAML frontmatter requirements
- [anthropics/skills package_skill.py](https://github.com/anthropics/skills/blob/main/skills/skill-creator/scripts/package_skill.py) - canonical exclusions, arcname convention, validation gates
- [claude.ai skill help center](https://support.claude.com/en/articles/12512180-use-skills-in-claude) - upload failure modes (zip size, folder-name mismatch, missing SKILL.md)
- PR #48 (2026-02) - the merge that introduced `vendor/package/`
- Open issues adjacent but out of scope: #239, #236, #231, #190, #184
- Related code: `scripts/lib/bird_x.py:5`, `tests/test_bird_x.py:133`, `.clawhubignore`, `scripts/sync.sh`, root `SKILL.md` frontmatter
- Private-repo reference pattern: `scripts/clawhub-publish.sh` + `scripts/strip_for_openclaw.py` - not copied here; `git archive` is simpler for the public path
