# Usage and rotation

## Prerequisites

```bash
# 0) Python 3.8+ and PyYAML (the only third-party dependency)
python3 -m pip install -r requirements.txt

# 1) persona Connect listener (unix socket; systemd example in the persona
#    repo docs/EXTERNAL_INTEGRATION.md step 2)
persona connect serve --unix-socket &

# 2) scoped read-only token (env only — never argv, never the file)
export PERSONA_CONNECT_TOKEN='pconn_…'   # scope llm-automation/api_key
```

### Persona minimum build

`fleet-apply` assumes a persona **build ≥ 2026-11-09**: that build (a) wires
`security.auto_lock_timeout` into the runtime (`0` = never auto-lock; this
workspace sets `0` in `config.toml`) and (b) refreshes the sensitive-operation
timer on authenticated Connect requests (a scoped `pconn_` token counts as
re-authentication). On older builds the vault may auto-lock mid-run and fail
with `503 vault_locked`.

Re-verifiable evidence (recorded live 2026-11-09):

1. `persona --version` → build date ≥ 2026-11-09.
2. `grep auto_lock_timeout` in the persona `config.toml` → expect `0`.
3. Unlock the vault, run `python3 fleet_apply.py --check` → green.
4. Idle ≥ **7 min** (no persona traffic), re-run `--check` → still green.

If step 4 fails with `503 vault_locked`, the persona build predates the fix —
upgrade before relying on unattended runs.

## Build and verify

There is no Makefile; the gates are two commands, and CI
(`.github/workflows/ci.yml`) runs exactly these on Python 3.12 with ruff
0.16.9 pinned:

```bash
python3 -m unittest discover -s tests   # 110 tests, offline
ruff check .
```

The docs site is a separate VitePress project driven by `package.json`
(`pnpm install`, then `pnpm docs:build`); deploying it is handled by
`.github/workflows/deploy-docs.yml`.

## Day to day

```bash
python3 fleet_apply.py --check            # verify everything, read-only
python3 fleet_apply.py --apply            # write managed blocks (0600), then verify
python3 fleet_apply.py --apply --force    # also overwrite conflicting outside values
```

Exit code is non-zero on any drift. Output contains names and conclusions only —
never key values.

## Rotation runbook

1. Rotate the key at the vendor (or `persona credential update …`).
2. Update the persona item value (`PERSONA_CREDENTIAL_SECRET` env / prompt).
3. `python3 fleet_apply.py --apply` — materials now carry the new value.
4. Restart consumers whose values changed (`systemctl --user restart litellm`;
   hermes picks `.env` up on next config load).
5. `python3 fleet_apply.py --check` — green = everywhere consistent.

## Release policy

Releases are manual and user-triggered; the CI has no release job and this
repository ships no binary artifacts, so there is nothing nightly to drift.
