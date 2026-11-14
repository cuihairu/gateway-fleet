# 已知缺口

对应 `fleet.yaml → gaps` 与 README 的同一节。默认策略：未经登记的明文
**一律失败**。

## `litellm_env_unmanaged`

`env.systemd` 中无 vault 覆盖的变量（非 LLM 密钥或尚未立项收录）：
`ALIBABA_CLOUD_API_KEY`、`NEWAPI_API_KEY`、`GITHUB_TOKEN`、`MAS_API_KEY`、
`DATABASE_URL`。

## `vault_no_consumer`

无消费方的 vault 条目——备用键：`qiniu-qn-api-key2`、`xiaomi-xiaomi-api-key`。

## `known_quirks`

`litellm-master-key` 的 vault 值带一层包裹引号（导入瑕疵）；
`fleet_apply.normalize` 在比对/落地时剥掉（`vault.normalize: strip-quotes`）。

## 已收口（2026-11-09）

`litellm/config.yaml` 的两行孤儿明文（同值 → 入库
`sensenova-sensenova-api-key3` → `os.environ/SENSENOVA_API_KEY3`）与
`hermes providers.ark.api_key`（== bifrost `ark-volces-main` → 入库 →
`${ARK_API_KEY}`）；litellm env 的 `ARK_API_KEY` 也已纳管。

## 校验强制面

| 规则 | 源码 |
| --- | --- |
| 明文 `api_key` 等于 vault 值 → 失败 | `fleet_apply.py:556-644` |
| 未登记的 hermes 明文 → 默认失败 | `fleet_apply.py:538-555`、`:746-782` |
| litellm 孤儿仅在登记为 `{model, count}` 时放行 | `fleet_apply.py:746-782` |
