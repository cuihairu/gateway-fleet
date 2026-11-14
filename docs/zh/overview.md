# 总览

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

## 目录结构

| 文件 | 职责 |
| --- | --- |
| `fleet.yaml` | 非密配置 + SecretRef（绝不含密钥值） |
| `fleet_apply.py` | 解析器 + 材料落盘 + 校验（`--check` / `--apply`） |
| `tests/test_fleet_apply.py` | 单测 + tempdir 集成测试（不触网、不碰真实材料） |
| `requirements.txt` | 第三方依赖（仅 PyYAML） |
| `.github/workflows/ci.yml` | CI 两道闸：ruff lint + 单元测试（无发版 job） |
| `docs/` | 本站点（英文 root，`/zh/` 简体中文） |

## 范围

- 仅限 LLM 舰队；`hermes .env` 里的非 LLM 密钥（消息机器人、peer key 等）
  不在范围内、原样不动。
- 仓库不存密钥值：`fleet.yaml`、文档与测试里只有引用、路径与结论。

## 下一步

- [功能清单](/zh/features)——`--check` / `--apply` 实际做什么（附代码定位）。
- [配置键与参数](/zh/keys)——`fleet.yaml` 全部键与 CLI 参数。
- [用法与轮换](/zh/usage)——前置、安装、轮换一把密钥。
