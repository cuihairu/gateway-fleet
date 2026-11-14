# Overview

`fleet.yaml` is the single source of truth for **non-secret** LLM-gateway
configuration; every secret lives in the persona vault (identity
`llm-automation`) and is referenced by SecretRef (`IDENTITY/ITEM/FIELD`,
resolved through the persona Connect API — no master password involved).

```
persona vault (llm-automation)          fleet.yaml                 consumers
┌──────────────────────┐   Connect API  ┌──────────────────┐  ┌─────────────────────┐
│ 24 api_key items     │ ─────────────► │ refs + non-secret │► │ litellm env.systemd │
│ (zai/kimi/sensenova/ │  unix socket   │ config (base_url, │  │ hermes .env + ${}   │
│  xiaomi/qiniu/…)     │  + pconn token │ consumers, gaps)  │  │ bifrost/magpie:     │
└──────────────────────┘                └──────────────────┘  │ read-only verify    │
                                                              └─────────────────────┘
```

## Repository layout

| file | role |
| --- | --- |
| `fleet.yaml` | non-secret config + SecretRefs (never contains key values) |
| `fleet_apply.py` | resolver + material writer + checker (`--check` / `--apply`) |
| `tests/test_fleet_apply.py` | unit + tempdir integration tests (no network, no real materials) |
| `requirements.txt` | third-party dependencies (PyYAML only) |
| `.github/workflows/ci.yml` | CI gates: ruff lint + unittest (no release job) |
| `docs/` | this VitePress site (English root, `/zh/` Simplified Chinese) |

## Scope

- Only the LLM fleet. Non-LLM secrets in `hermes .env` (messaging bots, peer
  keys, …) are out of scope and untouched.
- The repository never stores key values: `fleet.yaml`, docs and tests contain
  refs, paths and conclusions only.

## Next

- [Features](/features) — what `--check` and `--apply` actually do, with code
  pointers.
- [Config keys and flags](/keys) — every `fleet.yaml` key and CLI flag.
- [Usage and rotation](/usage) — prerequisites, install, rotate a key.
