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

## 再审计（G2，2026-11-14/15）

在 `main` `308c894`（工作树干净、与 `origin/main` 同步）上重跑。上表的计数属
上一轮，本轮不重数。

| 桶 | 计数 | P0 | P1 | P2 |
| --- | --- | --- | --- | --- |
| 文档超前于码 | 1 | 1 | 0 | 0 |
| 文档落后于码 | 0 | 0 | 0 | 0 |
| 描述不符 | 4 | 0 | 1 | 3 |

- **G2R-01（P0）**——`materials.*.restart_hint` 被写成工具会执行的行为（「变化时提示」）。
  `fleet_apply.py` 从不读取它：material spec 只用 `path`（409、658、834、836）与
  `mode`（659、837），唯一的 `[apply]` 输出在 909–918。已改为「已声明但尚未读取」。
- **G2R-02（P1）**——README 读起来像 Pages 地址已在线。实际不是：
  `gh api …/deployments` 返回 `[]`，连续 5 次 `Deploy Docs` 失败（最后一次死于
  pnpm 版本双声明，已修），`/` 与 `/zh/` 实探均 HTTP 404、无 `lang`、CJK 0。
  两份 README 已标注首次部署未落地。
- **G2R-03（P2）**——`vault.resolver` 写成像可选项；目前只有 `connect`，
  且 `fleet_apply.py:873` 硬编码 `ConnectResolver`。
- **G2R-04（P2）**——`gaps.hermes_plaintext` 形态漏记：`fleet_apply.py:760-769`
  与 `:981-989` 也接受裸路径字符串（按 1 处计）。
- **G2R-05（P2）**——上一轮台账写「16 页」；实测构建产出 **15** 页
  （英文 8 含 `404.html`、中文 7）。台账是日志不改写，改在本节记实测数。

计数类主张按 `fleet.yaml` 全部重算一致（24 条 providers；target 用量
18/17/5/3/3/1；env 变量 20 与 5 个），features 页约 25 处代码指针逐条比对命中。

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

## 收口（部署已落地）

G2R-02 由部署本身收口，而非再改措辞：三个构建 bug 依次修掉（pnpm 版本双声明
→ `308c894`；pnpm 12 默认 `strictDepBuilds` → `aacf16a`；`paths` 过滤表漏
`pnpm-workspace.yaml` → `f450194`），线上复测：

- `/` → 200，`lang="en-US"`，首屏 CJK 4（仅语言菜单标签）
- `/zh/` → 200，`lang="zh-CN"`，首屏 CJK 38
- `features.html` / `keys.html` / `audit.html` / `gaps.html` → 200，`en-US`，
  CJK 0（无跨语言串页）
- `zh/audit.html` → 200，`zh-CN`，CJK 38

README 里那句「首次部署仍未落地」在变绿后改回正常链接——否则那句本身就成了新的漂移。
