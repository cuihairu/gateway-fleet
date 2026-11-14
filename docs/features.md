# Features

Every row below is verified against the current source — the pointer column is
where the behaviour lives. Nothing here is a roadmap item.

## Resolution

| feature | where |
| --- | --- |
| SecretRef = `IDENTITY/ITEM/FIELD`, resolved over the persona Connect API (unix socket HTTP + scoped `pconn_` token, no master password) | `fleet_apply.py:278-330` (`ConnectResolver`), `:260-276` (`UnixHTTPConnection`) |
| `vault.endpoint` expansion (`${XDG_RUNTIME_DIR:-…}`), `vault.token_env` read from the environment — the token never comes from argv or a file | `fleet_apply.py:69-84`, `:862-870` |
| Path validation: rejects relative paths, `..` segments, symlink targets and symlinked ancestors before writing anything | `fleet_apply.py:89-109` |
| Secret-value guard: NUL / CR / newline values are rejected (they would break `render_value`), as is BEGIN/END marker text | `fleet_apply.py:39-58` |

## Managed blocks

| feature | where |
| --- | --- |
| Block markers `# >>> gateway-fleet managed (fleet-apply) >>>` … `# <<< gateway-fleet <<<` | `fleet_apply.py:34-35` |
| Structure checks: a second BEGIN, an END outside any block, or an unclosed BEGIN all fail the material instead of being silently rewritten | `fleet_apply.py:156-179` |
| Merge rules: matching outside lines move into the block; conflicting outside values are skipped and reported unless `--force`; a var missing from `fleet.yaml` keeps its old in-block value; an empty secret list refuses to rewrite | `fleet_apply.py:186-258` |

## Schema

| feature | where |
| --- | --- |
| Strict `fleet.yaml` schema: `version`, `vault.identity`, known targets, material specs, per-target required consumer fields, `gaps` and `check_targets` shapes — each failure names the exact key | `fleet_apply.py:712-847` |

## Read-only verification

| feature | where |
| --- | --- |
| bifrost `config_keys` / `governance_virtual_keys` checked against the persona value; the database opens as `file:…?mode=ro&immutable=1` so SQLite touches no journal/WAL/SHM (no side files) | `fleet_apply.py:441-472`, `:493-512` |
| magpie `caller-keys.json` caller secrets compared | `fleet_apply.py:513-526` |
| hermes config `${…}` refs compared against the expected ref (catches drift back to plaintext) | `fleet_apply.py:538-555` |
| Plaintext scan of the litellm and hermes configs: any `api_key` equal to a vault value fails; registered orphans in `gaps` are the only allowed plaintext | `fleet_apply.py:556-644` |
| Secret redaction: resolved values are registered for masking and stripped from every message, including error paths | `fleet_apply.py:382-397` |

## Write path (`--apply`)

| feature | where |
| --- | --- |
| `flock` on a sidecar `<material>.fleet-lock`, non-blocking retry (≤ ~5 s) so concurrent instances queue | `fleet_apply.py:649-675` |
| Full-content snapshot to `<material>.fleet-bak` (same mode) before any change — rollback is restoring that file | `fleet_apply.py:676-682` |
| Temp file in the same directory, `fsync`, then `os.replace`; the directory is `fsync`-ed too | `fleet_apply.py:683-693` |
| Refuse to empty: an empty secret list leaves the block untouched and reports the material as failed | `fleet_apply.py:186-258` |
| `--apply` then runs the full check pass, so a run never ends with unverified materials | `fleet_apply.py:905-921` |

## Commands

| command | what runs |
| --- | --- |
| `python3 fleet_apply.py --check` | schema → materials → read-only consumers → plaintext scan; non-zero exit on any drift |
| `python3 fleet_apply.py --apply` | writes the managed blocks (0600) then runs the same check pass |
| `python3 fleet_apply.py --apply --force` | as above, but conflicting outside values are overwritten too |
| `--fleet PATH` | point at another fleet file (default: the `fleet.yaml` next to the script) |

`--check` and `--apply` are a required mutually exclusive pair
(`fleet_apply.py:850-857`).

## Tests

`tests/test_fleet_apply.py` — 110 unit + tempdir integration tests, no network
and no real materials. CI (`.github/workflows/ci.yml`) runs the same two gates:
`python -m unittest discover -s tests` and `ruff check .` (pinned 0.16.9).
Releases are manual and user-triggered; CI has no release job.
