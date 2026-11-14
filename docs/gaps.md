# Known gaps

Mirror of `fleet.yaml → gaps` and the README section. Default policy: anything
plaintext that is not registered here **fails** the check.

## `litellm_env_unmanaged`

`env.systemd` variables with no vault coverage (not LLM secrets, or not yet
onboarded): `ALIBABA_CLOUD_API_KEY`, `NEWAPI_API_KEY`, `GITHUB_TOKEN`,
`MAS_API_KEY`, `DATABASE_URL`.

## `vault_no_consumer`

Vault items with no consumer — spare keys: `qiniu-qn-api-key2`,
`xiaomi-xiaomi-api-key`.

## `known_quirks`

`litellm-master-key` is stored in the vault with one layer of quotes (import
artifact); `fleet_apply.normalize` strips it on compare and materialise
(`vault.normalize: strip-quotes`).

## Closed (2026-11-09)

The two orphan plaintext rows in `litellm/config.yaml` (both held the same key
→ `sensenova-sensenova-api-key3` → `os.environ/SENSENOVA_API_KEY3`) and
`hermes providers.ark.api_key` (== bifrost `ark-volces-main` → imported →
`${ARK_API_KEY}`); `ARK_API_KEY` in litellm's env is managed too.

## What the check enforces

| rule | source |
| --- | --- |
| plaintext `api_key` equal to a vault value → failure | `fleet_apply.py:556-644` |
| unregistered hermes plaintext → failure by default | `fleet_apply.py:538-555`, `:746-782` |
| litellm orphans allowed only when declared as `{model, count}` | `fleet_apply.py:746-782` |
