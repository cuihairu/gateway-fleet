# gateway-fleet

> LLM 网关舰队的控制面配置：**密钥在 persona，配置在 fleet**。
> [中文](README.zh.md)

`fleet.yaml` is the single source of truth for **non-secret** LLM-gateway
configuration; every secret lives in the persona vault (identity
`llm-automation`) and is referenced by SecretRef (`IDENTITY/ITEM/FIELD`,
resolved through the persona Connect API — no master password involved).

```
persona vault (llm-automation)          fleet.yaml                 consumers
┌──────────────────────┐   Connect API  ┌──────────────────┐  ┌─────────────────────┐
│ 22 api_key items     │ ─────────────► │ refs + non-secret │► │ litellm env.systemd │
│ (zai/kimi/sensenova/ │  unix socket   │ config (base_url, │  │ hermes .env + ${}   │
│  xiaomi/qiniu/…)     │  + pconn token │ consumers, gaps)  │  │ bifrost/magpie:     │
└──────────────────────┘                └──────────────────┘  │ read-only verify    │
                                                              └─────────────────────┘
```

## Layout

| file | role |
| --- | --- |
| `fleet.yaml` | non-secret config + SecretRefs (never contains key values) |
| `fleet_apply.py` | resolver + material writer + checker (`--check` / `--apply`) |
| `tests/test_fleet_apply.py` | pure-function unit tests (no network, no real materials) |

## Prerequisites

```bash
# 1) persona Connect listener (unix socket; systemd example in persona
#    docs/EXTERNAL_INTEGRATION.md Step 2)
persona connect serve --unix-socket &

# 2) scoped read-only token (env only — never argv, never the file)
export PERSONA_CONNECT_TOKEN='pconn_…'   # scope llm-automation/api_key
```

> **Auto-lock window (read this)** — the vault session locks itself after
> **5 minutes of inactivity** (`PersonaService::auto_lock_timeout`, hardcoded
> 300 s; data-plane requests refresh it, `health` does not — and the
> `security.auto_lock_timeout` field in `config.toml` is currently *not* wired
> into `connect serve`). While locked, the data plane **and the Connect
> resolver** answer `503 vault_locked` until the vault is unlocked again.
> So run `--check`/`--apply` within the window after starting serve (a fresh
> `persona connect serve` starts unlocked). Keeping a headless serve
> permanently unlocked is an open persona design question (backlog).

## Usage

```bash
python3 fleet_apply.py --check    # verify: resolve + materials + refs + plaintext scan
python3 fleet_apply.py --apply    # write managed blocks (0600), then verify
python3 fleet_apply.py --apply --force   # overwrite conflicting outside-block values
python3 -m unittest discover -s tests    # unit tests
```

Exit code is non-zero on any drift. Output contains names and conclusions only —
never key values.

## What `--apply` manages

- **`litellm env.systemd`** — managed block (`# >>> gateway-fleet …`) holding the
  18 vault-covered variables; everything else in the file stays untouched.
  Restart `litellm` only when a value actually changes
  (`systemctl --user restart litellm`).
- **`hermes .env`** — managed block with `GLM_API_KEY`, `XIAOMI_API_KEY`,
  `BIFROST_VK_MAIN`, `LITELLM_MASTER_KEY`. hermes re-reads `.env` on config load.

Read-only verification targets: **bifrost** `config_keys` / `governance_virtual_keys`,
**magpie** `caller-keys.json`, **hermes config** `${…}` refs, and a
**plaintext scan** of `litellm/config.yaml` + `hermes/config.yaml` (any api_key
equal to a vault value is a failure; registered orphans in `gaps` are the only
allowed plaintext).

## Rotation runbook

1. Rotate the key at the vendor (or `persona credential update …`).
2. Update the persona item value (`PERSONA_CREDENTIAL_SECRET` env / prompt).
3. `python3 fleet_apply.py --apply` — materials now carry the new value.
4. Restart consumers whose values changed (`systemctl --user restart litellm`;
   hermes picks `.env` up on next config load).
5. `python3 fleet_apply.py --check` — green = everywhere consistent.

## Known gaps (tracked in `fleet.yaml → gaps`)

- 2 orphan plaintext rows in `litellm/config.yaml` (glm-5.3-flash ×1,
  kimi-k3-sensenova ×1) — key not yet in the vault.
- `hermes providers.ark.api_key` (== bifrost `ark-volces-main`, not imported).
- `litellm env.systemd` vars with no vault coverage (ALIBABA/NEWAPI/GITHUB/MAS/ARK/DATABASE_URL).
- Two spare vault items with no consumer (`qiniu-qn-api-key2`, `xiaomi-xiaomi-api-key`).
- `litellm-master-key` is stored in the vault with one layer of quotes (import
  artifact); `fleet_apply.normalize` strips it on compare/materialize.

## Security notes

- No secret values in this repository — `fleet.yaml` holds refs and config only.
- Materials are written `0600`; the connect token only ever comes from
  `PERSONA_CONNECT_TOKEN`; keys never enter argv or logs.
- Scope: only the LLM fleet. Non-LLM secrets in `hermes .env` (messaging bots,
  peer keys, …) are out of scope and untouched.
