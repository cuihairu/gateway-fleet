# gateway-fleet

> LLM 网关舰队的控制面配置：**密钥在 persona，配置在 fleet**。
> [English](README.md)

`fleet.yaml` 是**非密** LLM 网关配置的唯一事实源；所有密钥都在 persona
保险库（身份 `llm-automation`），以 SecretRef（`IDENTITY/ITEM/FIELD`，经
persona Connect API 解析——**不经过主密码**）引用。

```
persona 保险库 (llm-automation)        fleet.yaml                 消费方
┌──────────────────────┐   Connect API  ┌──────────────────┐  ┌─────────────────────┐
│ 22 条 api_key 条目    │ ─────────────► │ 引用 + 非密配置   │► │ litellm env.systemd │
│ (zai/kimi/sensenova/ │  unix socket   │ (base_url、消费方、│  │ hermes .env + ${}   │
│  xiaomi/qiniu/…)     │  + pconn token │  gaps)           │  │ bifrost/magpie:     │
└──────────────────────┘                └──────────────────┘  │ 只读校验            │
                                                              └─────────────────────┘
```

## 目录

| 文件 | 职责 |
| --- | --- |
| `fleet.yaml` | 非密配置 + SecretRef（绝不含密钥值） |
| `fleet_apply.py` | 解析器 + 材料落盘 + 校验（`--check` / `--apply`） |
| `tests/test_fleet_apply.py` | 纯函数单测（不触网、不碰真实材料） |

## 前置

```bash
# 1) persona Connect 监听（unix socket；systemd 样例见 persona
#    docs/EXTERNAL_INTEGRATION.zh.md 第 2 步）
persona connect serve --unix-socket &

# 2) 作用域只读 token（只走 env——绝不进 argv、不落文件）
export PERSONA_CONNECT_TOKEN='pconn_…'   # scope llm-automation/api_key
```

> **自动锁窗口（必读）** —— 保险库会话**空闲 5 分钟即自动锁定**
> （`PersonaService::auto_lock_timeout`，硬编码 300s；数据面请求会续命、
> `health` 不会 —— 且 `config.toml` 的 `security.auto_lock_timeout` 字段
> **目前并未接进** `connect serve`）。锁定期间数据面**与 Connect 解析器**
> 一律 `503 vault_locked`，直到再次解锁。因此 `--check`/`--apply` 要在
> 启动 serve 后的窗口内执行（刚启动的 `persona connect serve` 处于解锁态）。
> 让 headless serve 永久保持解锁是 persona 侧的待决设计问题（待办池）。

## 用法

```bash
python3 fleet_apply.py --check    # 校验：解析 + 材料 + 引用 + 明文扫描
python3 fleet_apply.py --apply    # 写托管块（0600），随后自动校验
python3 fleet_apply.py --apply --force   # 覆盖块外不一致的旧值
python3 -m unittest discover -s tests    # 单元测试
```

任何漂移都会非零退出；输出只有名称与结论——**绝不打印密钥值**。

## `--apply` 管什么

- **`litellm env.systemd`** —— 托管块（`# >>> gateway-fleet …`）承载 vault
  覆盖的 18 个变量，文件其余部分原样保留。仅当值实际变化时才需要
  `systemctl --user restart litellm`。
- **`hermes .env`** —— 托管块含 `GLM_API_KEY`、`XIAOMI_API_KEY`、
  `BIFROST_VK_MAIN`、`LITELLM_MASTER_KEY`；hermes 加载配置时重读 `.env`。

只读校验目标：**bifrost** `config_keys` / `governance_virtual_keys`、
**magpie** `caller-keys.json`、**hermes config** 的 `${…}` 引用，以及
`litellm/config.yaml` + `hermes/config.yaml` 的**明文扫描**（任何 api_key
明文等于 vault 值即失败；`gaps` 登记的孤儿是唯一允许的明文）。

## 轮换手册

1. 在厂商侧轮换密钥（或 `persona credential update …`）。
2. 更新 persona 条目值（`PERSONA_CREDENTIAL_SECRET` env / 交互提示）。
3. `python3 fleet_apply.py --apply` —— 材料带上新值。
4. 重启值有变化的消费方（`systemctl --user restart litellm`；hermes 下次
   加载配置即拾取 `.env`）。
5. `python3 fleet_apply.py --check` —— 全绿 = 各处一致。

## 已知缺口（`fleet.yaml → gaps` 登记）

- `litellm/config.yaml` 2 行孤儿明文（glm-5.3-flash ×1、kimi-k3-sensenova ×1）
  —— key 尚未入库。
- `hermes providers.ark.api_key`（== bifrost `ark-volces-main`，未导入）。
- `litellm env.systemd` 无 vault 覆盖的变量（ALIBABA/NEWAPI/GITHUB/MAS/ARK/DATABASE_URL）。
- 两条无消费方的备用条目（`qiniu-qn-api-key2`、`xiaomi-xiaomi-api-key`）。
- `litellm-master-key` 的 vault 值带一层包裹引号（导入瑕疵）；
  `fleet_apply.normalize` 在比对/落地时剥掉。

## 安全说明

- 本仓库无任何密钥值——`fleet.yaml` 只有引用与配置。
- 材料文件写入 `0600`；connect token 只从 `PERSONA_CONNECT_TOKEN` 读取；
  密钥不进 argv、不进日志。
- 范围仅限 LLM 舰队；`hermes .env` 里的非 LLM 密钥（消息机器人、peer
  key 等）不在范围内、原样不动。
