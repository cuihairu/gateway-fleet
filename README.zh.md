# gateway-fleet

> LLM 网关舰队的控制面配置：**密钥在 persona，配置在 fleet**。
> [English](README.md)

`fleet.yaml` 是**非密** LLM 网关配置的唯一事实源；所有密钥都在 persona
保险库（身份 `llm-automation`），以 SecretRef（`IDENTITY/ITEM/FIELD`，经
persona Connect API 解析——**不经过主密码**）引用。

```
persona 保险库 (llm-automation)        fleet.yaml                 消费方
┌──────────────────────┐   Connect API  ┌──────────────────┐  ┌─────────────────────┐
│ 24 条 api_key 条目    │ ─────────────► │ 引用 + 非密配置   │► │ litellm env.systemd │
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
| `tests/test_fleet_apply.py` | 单测 + tempdir 集成测试（不触网、不碰真实材料） |
| `requirements.txt` | 第三方依赖（仅 PyYAML） |

## 前置

```bash
# 0) Python 3.8+ 与 PyYAML（唯一第三方依赖）
python3 -m pip install -r requirements.txt

# 1) persona Connect 监听（unix socket；systemd 样例见 persona
#    docs/EXTERNAL_INTEGRATION.zh.md 第 2 步）
persona connect serve --unix-socket &

# 2) 作用域只读 token（只走 env——绝不进 argv、不落文件）
export PERSONA_CONNECT_TOKEN='pconn_…'   # scope llm-automation/api_key
```

> **两个空闲窗口均已关闭（persona ≥ 2026-11-09）** —— `security.
> auto_lock_timeout` 已接进运行时（`0` = 永不；本工作区 `config.toml` 已设
> `0`），且经鉴权的 Connect 请求会刷新敏感操作计时（scoped token 即再认
> 证）。真机验证：空闲 **7 分钟**后 `--check` 全绿，health、数据面与读密
> 文全部正常应答。

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
  覆盖的 20 个变量（ARK_API_KEY、DEEPSEEK_API_KEY、LITELLM_MASTER_KEY、
  MIMO_API_KEY、MOONSHOT_API_KEY、NVIDIA_NIM_API_KEY、OPENCODE_GO_KEY、
  QN_API_KEY/2/3、SENSENOVA_API_KEY/2/3、SENSENOVA_A3–A7_KEY、XIAOMI_API_KEY、
  ZAI_AUTH_TOKEN），文件其余部分原样保留。仅当值实际变化时才需要
  `systemctl --user restart litellm`。
- **`hermes .env`** —— 托管块含 vault 覆盖的 5 个变量：`ARK_API_KEY`、
  `BIFROST_VK_MAIN`、`GLM_API_KEY`、`LITELLM_MASTER_KEY`、`XIAOMI_API_KEY`；
  hermes 加载配置时重读 `.env`。

### 写入安全

- **文件锁** —— `--apply` 动任何文件前先在 `<材料>.fleet-lock` 上取 `flock`，
  并发实例排队而不是交错写（无 lost update）。
- **变更前快照** —— 改动前把整份旧内容拷到 `<材料>.fleet-bak`（同 mode），
  回滚即还原该文件。
- **原子替换** —— 内容先写同目录临时文件、`fsync` 后用 `os.replace` 覆盖，
  读方永远看不到半写文件。
- **拒绝清空** —— 重组后没有任何密钥可写时（`fleet.yaml` 有问题）保持托管块
  原样，并把该材料报成失败，绝不把块抹空。

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

2026-11-09 已收口：`litellm/config.yaml` 的2 行孤儿明文（同值 → 入库
`sensenova-sensenova-api-key3` → `os.environ/SENSENOVA_API_KEY3`）、
`hermes providers.ark.api_key`（== bifrost `ark-volces-main` → 入库 →
`${ARK_API_KEY}`）；litellm env 的 `ARK_API_KEY` 也已纳管。

仍开放：

- `litellm env.systemd` 无 vault 覆盖的变量（ALIBABA/NEWAPI/GITHUB/MAS/DATABASE_URL）。
- 两条无消费方的备用条目（`qiniu-qn-api-key2`、`xiaomi-xiaomi-api-key`）。
- `litellm-master-key` 的 vault 值带一层包裹引号（导入瑕疵）；
  `fleet_apply.normalize` 在比对/落地时剥掉。

## 安全说明

- 本仓库无任何密钥值——`fleet.yaml` 只有引用与配置。
- 材料文件写入 `0600`；connect token 只从 `PERSONA_CONNECT_TOKEN` 读取；
  密钥不进 argv、不进日志。
- 范围仅限 LLM 舰队；`hermes .env` 里的非 LLM 密钥（消息机器人、peer
  key 等）不在范围内、原样不动。
