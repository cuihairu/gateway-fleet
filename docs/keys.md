# Config keys and flags

Every key below exists in the current `fleet.yaml` — there is no forward-looking
entry. Counts are recomputed from the file at audit time.

## Top level

| key | required | meaning |
| --- | --- | --- |
| `version` | yes | schema version, currently `1` |
| `vault` | yes | where secrets come from and how they are resolved |
| `materials` | yes | writable files and how their managed block is written |
| `check_targets` | no | paths for the read-only checks (overrides the built-in defaults; a consumer may still override per-entry with `db` / `file`) |
| `providers` | yes | the SecretRef → consumer wiring (24 items today) |
| `gaps` | no | registered exceptions for the plaintext scan and the env coverage audit |

## `vault`

| key | meaning |
| --- | --- |
| `identity` | persona identity holding the keys — `llm-automation` |
| `resolver` | `connect`: the unix-socket Connect API (no master password) |
| `endpoint` | unix socket, env-expandable (`${XDG_RUNTIME_DIR:-/run/user/1000}`) |
| `token_env` | name of the environment variable holding the scoped `pconn_` token — the token never sits in the file |
| `normalize` | `strip-quotes`: unwrap the import artifact on `litellm-master-key` before comparing and materialising |

## `materials`

One entry per writable file, each with:

| key | meaning |
| --- | --- |
| `path` | absolute-or-`~` path; relative paths, `..` segments, symlinks and symlinked ancestors are rejected |
| `mode` | octal mode string written on create (materials are `0600`) |
| `restart_hint` | shown on change; `null` for consumers that re-read on config load |

Current materials: `litellm-env` → `~/.local/share/litellm/env.systemd`,
`hermes-env` → `~/.hermes/.env`.

## Consumer targets

| target | required keys | effect |
| --- | --- | --- |
| `litellm-env` | `vars` | writes the named variables into that material's managed block |
| `hermes-env` | `vars` | as above |
| `hermes-config` | `ref`, `paths` | verifies each yaml path still carries the `${…}` ref instead of plaintext |
| `bifrost-key` | `name` | read-only: `config_keys` row of the same name equals the persona value |
| `bifrost-vk` | `name` | read-only: `governance_virtual_keys` row of the same name |
| `magpie-caller` | `name` | read-only: the caller's secret in `caller-keys.json` |

Target usage in the current file: `litellm-env` 18, `bifrost-key` 17,
`hermes-env` 5, `hermes-config` 3, `magpie-caller` 3, `bifrost-vk` 1.

## `gaps`

| key | shape | meaning |
| --- | --- | --- |
| `litellm_plaintext_orphans` | list of `{model, count}` | plaintext models with no vault key — the only allowed litellm plaintext, counted per model |
| `hermes_plaintext` | list of `{path, count}` | allowed hermes plaintext paths; anything unregistered fails by default |
| `litellm_env_unmanaged` | list of names | `env.systemd` variables with no vault coverage |
| `vault_no_consumer` | list of item ids | vault items with no consumer (spare keys) |
| `known_quirks` | list of strings | free-text notes (e.g. the quoted `litellm-master-key` value) |

## Coverage numbers

| material | vault-covered variables |
| --- | --- |
| `litellm-env` | 20 |
| `hermes-env` | 5 — `ARK_API_KEY`, `BIFROST_VK_MAIN`, `GLM_API_KEY`, `LITELLM_MASTER_KEY`, `XIAOMI_API_KEY` |

## CLI flags

| flag | meaning |
| --- | --- |
| `--check` | verify only (read-only); mutually exclusive with `--apply`, one is required |
| `--apply` | write the managed blocks (0600), then run the same check pass |
| `--force` | also overwrite conflicting values outside the managed block |
| `--fleet PATH` | use another fleet file (default: the `fleet.yaml` next to the script) |

Exit code is non-zero on any drift. Output contains names and conclusions only.
