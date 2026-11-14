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
│ 24 api_key items     │ ─────────────► │ refs + non-secret │► │ litellm env.systemd │
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
| `tests/test_fleet_apply.py` | unit + tempdir integration tests (no network, no real materials) |
| `requirements.txt` | third-party dependencies (PyYAML only) |

## Prerequisites

```bash
# 0) Python 3.8+ and PyYAML (the only third-party dependency)
python3 -m pip install -r requirements.txt

# 1) persona Connect listener (unix socket; systemd example in persona
#    docs/EXTERNAL_INTEGRATION.md Step 2)
persona connect serve --unix-socket &

# 2) scoped read-only token (env only — never argv, never the file)
export PERSONA_CONNECT_TOKEN='pconn_…'   # scope llm-automation/api_key
```

### Persona minimum build & idle-window verification (security precondition)

fleet-apply assumes a persona **build ≥ 2026-11-09**: that build (a) wires
`security.auto_lock_timeout` into the runtime (`0` = never auto-lock; this
workspace sets `0` in `config.toml`) and (b) refreshes the
sensitive-operation timer on authenticated Connect requests (the scoped
`pconn_` token counts as re-authentication). On older builds the vault may
auto-lock mid-run and fail with `503 vault_locked`.

Re-verifiable evidence (recorded live 2026-11-09):

1. `persona --version` → confirm build date ≥ 2026-11-09.
2. `grep auto_lock_timeout` in the persona `config.toml` → expect `0`.
3. Unlock the vault, run `python3 fleet_apply.py --check` → green.
4. Idle ≥ **7 min** (no persona traffic), re-run `--check` → still green;
   `health`, the data plane and secret reads all answering.

If step 4 fails with `503 vault_locked`, the persona build predates the fix —
upgrade before relying on unattended runs.

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
  20 vault-covered variables (ARK_API_KEY, DEEPSEEK_API_KEY, LITELLM_MASTER_KEY,
  MIMO_API_KEY, MOONSHOT_API_KEY, NVIDIA_NIM_API_KEY, OPENCODE_GO_KEY,
  QN_API_KEY/2/3, SENSENOVA_API_KEY/2/3, SENSENOVA_A3–A7_KEY, XIAOMI_API_KEY,
  ZAI_AUTH_TOKEN); everything else in the file stays untouched.
  Restart `litellm` only when a value actually changes
  (`systemctl --user restart litellm`).
- **`hermes .env`** — managed block with the 5 vault-covered variables:
  `ARK_API_KEY`, `BIFROST_VK_MAIN`, `GLM_API_KEY`, `LITELLM_MASTER_KEY`,
  `XIAOMI_API_KEY`. hermes re-reads `.env` on config load.

### Write safety

- **Lock** — `--apply` takes a `flock` on a sidecar `<material>.fleet-lock`
  before touching anything, so concurrent instances queue instead of
  interleaving (no lost update).
- **Snapshot** — the whole previous content is copied to
  `<material>.fleet-bak` (same `mode`) before any change; rollback is restoring
  that file.
- **Atomic replace** — content goes to a temp file in the same directory, is
  `fsync`-ed, then moved over the material with `os.replace`; readers never see
  a half-written file.
- **Refuse to empty** — if the merge ends up with no secrets to write (bad
  `fleet.yaml`) the block is left untouched and the material reported as a
  failure instead of the block being wiped.

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

Closed 2026-11-09: the 2 orphan plaintext rows in `litellm/config.yaml`
(both held the same key → `sensenova-sensenova-api-key3` →
`os.environ/SENSENOVA_API_KEY3`) and `hermes providers.ark.api_key`
(== bifrost `ark-volces-main` → imported → `${ARK_API_KEY}`); `ARK_API_KEY`
in litellm's env is now managed too.

Still open:

- `litellm env.systemd` vars with no vault coverage (ALIBABA/NEWAPI/GITHUB/MAS/DATABASE_URL).
- Two spare vault items with no consumer (`qiniu-qn-api-key2`, `xiaomi-xiaomi-api-key`).
- `litellm-master-key` is stored in the vault with one layer of quotes (import
  artifact); `fleet_apply.normalize` strips it on compare/materialize.

## Security notes

- No secret values in this repository — `fleet.yaml` holds refs and config only.
- Materials are written `0600`; the connect token only ever comes from
  `PERSONA_CONNECT_TOKEN`; keys never enter argv or logs.
- Scope: only the LLM fleet. Non-LLM secrets in `hermes .env` (messaging bots,
  peer keys, …) are out of scope and untouched.
