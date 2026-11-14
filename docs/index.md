---
layout: home
hero:
  name: gateway-fleet
  text: Control-plane config for an LLM gateway fleet
  tagline: Non-secret config in fleet.yaml; every secret stays in the persona vault.
  actions:
    - theme: brand
      text: Overview
      link: /overview
    - theme: alt
      text: GitHub
      link: https://github.com/cuihairu/gateway-fleet
features:
  - icon: 🔐
    title: Secrets never land here
    details: fleet.yaml holds SecretRefs only. Values are resolved through the persona Connect API (unix socket + scoped pconn token) — no master password, no key values in this repository.
  - icon: 🛡️
    title: Safe writes
    details: --apply locks the material sidecar, snapshots the old content to <material>.fleet-bak, then replaces the file atomically. An empty secret list is refused instead of wiping the block.
  - icon: 📖
    title: Read-only verify
    details: bifrost keys, magpie caller keys, hermes ${…} refs and a plaintext scan of litellm / hermes configs — every check reports names and conclusions only.
---