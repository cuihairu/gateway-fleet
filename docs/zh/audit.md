# 文档与源码一致性审计

审计口径：`~/workspaces/standards/文档与源码一致性审计规范.md`（六步 + 三桶）。
修复方向：一律**改文档对齐代码**，不改代码迁就文档。带 `file:line` 证据的
完整 ID 表在内部台账：
[`docs-audit/审计-文档一致性.md`](https://github.com/cuihairu/gateway-fleet/blob/main/docs-audit/%E5%AE%A1%E8%AE%A1-%E6%96%87%E6%A1%A3%E4%B8%80%E8%87%B4%E6%80%A7.md)。
本页只放摘要。

## 三桶计数（G2 轮，2026-11-14）

| 桶 | 判定 | 计数 |
| --- | --- | --- |
| 文档超前于码（幽灵功能） | 文档声称代码没有的行为 | 0 |
| 文档落后于码（缺失） | 代码/仓库已有面，文档未覆盖 | 3（`ci.yml`、`todo.md`、`docs/` 未列） |
| 描述不符（措辞漂移） | 有覆盖但措辞与真实行为不符 | 2（`--apply` 措辞、`--force` 范围） |

五条全部为 P2，均已修。文档里的计数——24 个 providers、`litellm-env` 20 个
变量、`hermes-env` 5 个变量——按 `fleet.yaml` 重算一致。

## 已核无漂移面

- Python 3.8+ 声明 ↔ `ruff.toml` `target-version = "py38"`、`from __future__ import annotations`、运行时无 3.9+ 内置泛型字面量。
- 写安全四条保证 ↔ `fleet_apply.py:649-695`（flock 旁路锁、`.fleet-bak` 快照、tmp+`fsync`+`os.replace`、空清单拒写）。
- 只读目标 4 项 ↔ `check_targets`（`fleet.yaml:31-35`）↔ `fleet_apply.py:933-959`。
- `fleet.yaml` 使用的每个 `target:` 都在 `_validate_consumer` 白名单内（`fleet_apply.py:703-708`）。
- `README*`、`todo.md`、`docs/` 无密钥值（`sk-` / `pconn_` / 长 hex 扫描 0 命中）；persona 文档跨仓链接仍在。
- 无徽章、无 Download 节、无二进制产物 → 夜间产物漂移面在本仓不适用。

## 浏览器语言自动识别

本轮开始时本仓**无文档站**（两个 URL 均 404），因此走受规的成对文件形态：
`README.md`（英）↔ `README.zh.md`（中）。本轮建站后，locale 路由形态同时
成立：`/` 英文、`/zh/` 简体中文，两种语言都带手动语言菜单（`langMenuLabel`）。

**自动切换未实现**——配置内无 `navigator.language` 探测、无 `rootLocale`
重定向，中文浏览器仍落在英文首页。手动切换是受规基线；自动识别是增强项，
已立为 P2 todo。范围属舰队级拍板项，不单属本仓。
