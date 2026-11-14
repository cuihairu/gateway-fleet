# Docs consistency audit

Method: `~/workspaces/standards/文档与源码一致性审计规范.md` (six steps, three
buckets). Fix direction is always **docs to match code** — never code bent to
fit docs. The full IDed table (with `file:line` evidence) lives in the internal
ledger: [`docs-audit/审计-文档一致性.md`](https://github.com/cuihairu/gateway-fleet/blob/main/docs-audit/%E5%AE%A1%E8%AE%A1-%E6%96%87%E6%A1%A3%E4%B8%80%E8%87%B4%E6%80%A7.md).
This page only carries the summary.

## Three-bucket counts (round G2, 2026-11-14)

| bucket | meaning | count |
| --- | --- | --- |
| docs ahead of code (ghost features) | docs claim behaviour the code lacks | 0 |
| docs behind code (missing) | code/repo surface not covered | 3 (`ci.yml`, `todo.md`, `docs/` unlisted) |
| description mismatch (wording drift) | covered but phrasing disagrees | 2 (`--apply` wording, `--force` scope) |

All five were P2 and are fixed. Counts quoted in the docs — 24 providers,
20 `litellm-env` variables, 5 `hermes-env` variables — were recomputed from
`fleet.yaml` and agree.

## Re-audit (G2, 2026-11-14/15)

Re-run against `main` at `308c894` (tree clean, in sync with `origin/main`).
Counts above are the earlier round and are not re-counted here.

| bucket | count | P0 | P1 | P2 |
| --- | --- | --- | --- | --- |
| docs ahead of code | 1 | 1 | 0 | 0 |
| docs behind code | 0 | 0 | 0 | 0 |
| description mismatch | 4 | 0 | 1 | 3 |

- **G2R-01 (P0)** — `materials.*.restart_hint` was documented as something the
  tool *does* ("shown on change"). `fleet_apply.py` never reads it: material
  specs are consulted for `path` only (409, 658, 834, 836) and `mode`
  (659, 837); the only `[apply]` output lines are 909–918. Documented as
  declared-but-unread (P0 per policy).
- **G2R-02 (P1)** — the READMEs read as if the Pages URL were live. It is not:
  `gh api …/deployments` returns `[]`, five consecutive `Deploy Docs` runs
  failed (the last one dying on a duplicate pnpm version declaration, since
  fixed), and both `/` and `/zh/` probe as HTTP 404 with no `lang` and no CJK.
  Both READMEs now say the first deploy is pending.
- **G2R-03 (P2)** — `vault.resolver` presented as a choice; only `connect`
  exists and `fleet_apply.py:873` hardcodes `ConnectResolver`.
- **G2R-04 (P2)** — `gaps.hermes_plaintext` shape understated;
  `fleet_apply.py:760-769` and `:981-989` also accept a bare path string
  (counted as one occurrence).
- **G2R-05 (P2)** — the earlier ledger said 16 built pages; the build
  produces **15** (8 English incl. `404.html`, 7 Chinese). Ledger is a log,
  so it was left as written and the real count is recorded instead.

Every numeric claim was recomputed from `fleet.yaml` and agrees (24 providers;
target usage 18/17/5/3/3/1; 20 and 5 unique env vars), and all ~25 code
pointers in the features pages were checked line by line.

## Verified surfaces (no drift)

- Python 3.8+ claim ↔ `ruff.toml` `target-version = "py38"`, `from __future__ import annotations`, no 3.9+ literal generics at runtime.
- The four write-safety guarantees ↔ `fleet_apply.py:649-695` (`flock` sidecar lock, `.fleet-bak` snapshot, temp+`fsync`+`os.replace`, refuse-to-empty).
- The four read-only targets ↔ `check_targets` (`fleet.yaml:31-35`) ↔ `fleet_apply.py:933-959`.
- Every `target:` used in `fleet.yaml` is in the `_validate_consumer` allow-list (`fleet_apply.py:703-708`).
- No key values in `README*`, `todo.md` or `docs/` (`sk-` / `pconn_` / long-hex scan: 0 hits); the persona doc cross-links still exist.
- No badges, no Download section, no binaries → the nightly-artifact drift surface does not apply.

## Browser language auto-detection

The repo started this round with **no docs site** (both URLs 404), so the
sanctioned paired-file form applied: `README.md` (English) ↔ `README.zh.md`.
This round adds the site, so locale routing now also holds: `/` English, `/zh/`
Simplified Chinese, with a manual language menu (`langMenuLabel`) in both.

**Auto-switch is not implemented** — no `navigator.language` detection and no
`rootLocale` redirect, so a Chinese browser still lands on the English page.
Manual switching is the sanctioned baseline; auto-detection is an enhancement
and is tracked as a P2 todo. Scope is a fleet-wide decision, not a single-repo
one.

## Closure (deploy landed)

G2R-02 is now resolved by deployment rather than by further wording: the three
build bugs (duplicate pnpm version → `308c894`; pnpm 12 `strictDepBuilds`
default → `aacf16a`; `paths` filter missing `pnpm-workspace.yaml` → `f450194`)
were fixed in that order, and the live probes came back

| URL | status | `<html lang>` | whole-page CJK | of which content |
| --- | --- | --- | --- | --- |
| `/` | 200 | `en-US` | 113 | **0** |
| `/features` `/keys` `/gaps` `/usage` | 200 | `en-US` | 113 | **0** |
| `/audit` | 200 | `en-US` | 132 | 19 |
| `/zh/` | 200 | `zh-CN` | 298 | 170 |
| `/zh/features` | 200 | `zh-CN` | 874 | 700 |
| `/zh/keys` | 200 | `zh-CN` | 846 | 671 |
| `/zh/audit` | 200 | `zh-CN` | — | — |

Every CJK character on an English page is page chrome — the zh sidebar entries
and the language menu label. Content CJK is **0** on all English pages except
`/audit`, where the 19 characters are the two literal Chinese *file names* in the
method link at the top of the page (12 + 8). No translated content crosses
locales.

The counts are whole characters, not CJK strings. Counting CJK in the *first
2000 bytes* is not usable here: `<head>` and nav vary in length per page, so the
same page reads differently at different byte offsets. Whole-page counts with a
nav/`<head>` split are deterministic, so that is what is asserted. (The earlier
"first-screen CJK" figures in the closure notes were such an artifact and are
superseded.)

The `/audit` rows are self-referential — the page that renders the numbers also
carries the prose describing them — so they were read back from the built HTML
after that prose was final, and this caveat is ASCII-only on purpose so it cannot
move the English figures. It briefly named those two file paths again in the
prose, which added 12 + 8 characters and made the English content count read 38
instead of 19: the measurement reacting to its own write-up, not the page
changing. The English figures are stable across rebuilds for that reason.

`/zh/audit` is deliberately left unpinned: its caveat must be written in
Chinese on that very page, so rewording it moves the count it reports — its
content CJK read 1059, 1184, 1173, 1194 and 1183 across the rebuilds while this
note was edited. Not asserting a figure that moves under the reader beats
asserting a wrong one. The observed values are logged in the internal ledger,
which is not part of the site build and so cannot self-reference.

The README line that said the first deploy was still pending was reworded back
to a live URL once it went green — that wording would itself have been drift.
