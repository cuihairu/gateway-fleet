# 用法与轮换

## 前置

```bash
# 0) Python 3.8+ 与 PyYAML（唯一第三方依赖）
python3 -m pip install -r requirements.txt

# 1) persona Connect 监听（unix socket；systemd 样例见 persona 仓
#    docs/EXTERNAL_INTEGRATION.zh.md 第 2 步）
persona connect serve --unix-socket &

# 2) 作用域只读 token（只走 env——绝不进 argv、不落文件）
export PERSONA_CONNECT_TOKEN='pconn_…'   # scope llm-automation/api_key
```

### persona 最低版本与空闲窗口复验

`fleet-apply` 依赖 persona **≥ 2026-11-09 构建**：该构建 (a) 把
`security.auto_lock_timeout` 接进运行时（`0` = 永不自动锁；本工作区
`config.toml` 已设 `0`），(b) 经鉴权的 Connect 请求会刷新敏感操作计时
（scoped `pconn_` token 即再认证）。更旧的构建可能在运行途中自动锁库，
以 `503 vault_locked` 失败。

可复验的证据（2026-11-09 真机录得）：

1. `persona --version` → 构建日期 ≥ 2026-11-09。
2. persona `config.toml` 中 `grep auto_lock_timeout` → 应为 `0`。
3. 解锁保险库，跑 `python3 fleet_apply.py --check` → 全绿。
4. 空闲 ≥ **7 分钟**（无 persona 流量）后重跑 `--check` → 仍全绿。

第 4 步若得到 `503 vault_locked`，说明 persona 构建早于修复——先升级，
再谈无人值守运行。

## 构建与校验

本仓没有 Makefile；闸口就两条命令，CI（`.github/workflows/ci.yml`）
在 Python 3.12 + ruff 0.16.9 下跑的是同样这两条：

```bash
python3 -m unittest discover -s tests   # 110 例，离线
ruff check .
```

文档站是独立的 VitePress 工程，由 `package.json` 驱动
（`pnpm install` 后 `pnpm docs:build`）；部署由
`.github/workflows/deploy-docs.yml` 负责。

## 日常

```bash
python3 fleet_apply.py --check            # 全量校验，只读
python3 fleet_apply.py --apply            # 写托管块（0600），随后自动转入校验
python3 fleet_apply.py --apply --force    # 连块外不一致的旧值一并覆盖
```

任何漂移都会非零退出；输出只有名称与结论——**绝不打印密钥值**。

## 轮换手册

1. 在厂商侧轮换密钥（或 `persona credential update …`）。
2. 更新 persona 条目值（`PERSONA_CREDENTIAL_SECRET` env / 交互提示）。
3. `python3 fleet_apply.py --apply` —— 材料带上新值。
4. 重启值有变化的消费方（`systemctl --user restart litellm`；hermes 下次
   加载配置即拾取 `.env`）。
5. `python3 fleet_apply.py --check` —— 全绿 = 各处一致。

## 发版纪律

发版由用户手动触发；CI 无发版 job，本仓也不产出任何二进制，
因此不存在会漂的夜间构建产物。
