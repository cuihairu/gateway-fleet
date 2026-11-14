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
