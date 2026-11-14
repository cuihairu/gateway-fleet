# 功能清单

下表每一行都对照了当前源码——指针列就是该行为所在位置。**这里没有任何计划项。**

## 解析

| 功能 | 位置 |
| --- | --- |
| SecretRef = `IDENTITY/ITEM/FIELD`，经 persona Connect API（unix socket HTTP + 作用域 `pconn_` token，不经主密码）解析 | `fleet_apply.py:278-330`（`ConnectResolver`）、`:260-276`（`UnixHTTPConnection`） |
| `vault.endpoint` 展开（`${XDG_RUNTIME_DIR:-…}`）；`vault.token_env` 只从环境变量读——token 不进 argv、不落文件 | `fleet_apply.py:69-84`、`:862-870` |
| 路径校验：写任何文件前拒绝相对路径、`..` 段、符号链接目标与带软链祖先的路径 | `fleet_apply.py:89-109` |
| 密钥值守卫：含 NUL/CR/换行的值被拒绝（会破坏 `render_value`），BEGIN/END 标记文本同样被拒 | `fleet_apply.py:39-58` |

## 托管块

| 功能 | 位置 |
| --- | --- |
| 块标记 `# >>> gateway-fleet managed (fleet-apply) >>>` … `# <<< gateway-fleet <<<` | `fleet_apply.py:34-35` |
| 结构检查：第二处 BEGIN、块外 END、未闭合的 BEGIN 都会让该材料失败，而不是被静默重写 | `fleet_apply.py:156-179` |
| 合并规则：块外同名行一致则移入块；块外值冲突时跳过并报告（除非 `--force`）；不在 `fleet.yaml` 里的变量保留块内旧值；清单为空则拒绝重写 | `fleet_apply.py:186-258` |

## 模式（schema）

| 功能 | 位置 |
| --- | --- |
| `fleet.yaml` 严格模式：`version`、`vault.identity`、已知 target、材料规格、逐 target 的必填消费方字段、`gaps` 与 `check_targets` 形态——每条失败都点名具体键 | `fleet_apply.py:712-847` |

## 只读校验

| 功能 | 位置 |
| --- | --- |
| bifrost `config_keys` / `governance_virtual_keys` 与 persona 值比对；数据库以 `file:…?mode=ro&immutable=1` 打开，SQLite 完全不碰 journal/WAL/SHM（无旁路文件） | `fleet_apply.py:441-472`、`:493-512` |
| magpie `caller-keys.json` 中 caller 密钥比对 | `fleet_apply.py:513-526` |
| hermes 配置的 `${…}` 引用与期望 ref 比对（防退回明文） | `fleet_apply.py:538-555` |
| litellm 与 hermes 配置的明文扫描：任何 `api_key` 等于 vault 值即失败；`gaps` 登记的孤儿是唯一允许的明文 | `fleet_apply.py:556-644` |
| 密钥脱敏：解析出的值登记后从所有消息（含报错路径）中剥离 | `fleet_apply.py:382-397` |

## 写入路径（`--apply`）

| 功能 | 位置 |
| --- | --- |
| 在旁路文件 `<材料>.fleet-lock` 上取 `flock`，非阻塞重试（最多约 5 s），并发实例排队 | `fleet_apply.py:649-675` |
| 改动前把整份旧内容快照到 `<材料>.fleet-bak`（同 mode）——回滚即还原该文件 | `fleet_apply.py:676-682` |
| 内容先写同目录临时文件、`fsync`、再 `os.replace`；目录也 `fsync` | `fleet_apply.py:683-693` |
| 拒绝清空：清单为空时托管块原样保留，该材料报成失败 | `fleet_apply.py:186-258` |
| `--apply` 之后统一走 check 收口——运行结束时材料必然已校验 | `fleet_apply.py:905-921` |

## 命令

| 命令 | 行为 |
| --- | --- |
| `python3 fleet_apply.py --check` | 模式 → 材料 → 只读消费方 → 明文扫描；任何漂移非零退出 |
| `python3 fleet_apply.py --apply` | 写托管块（0600），随后跑同一遍校验 |
| `python3 fleet_apply.py --apply --force` | 同上，但块外不一致的旧值也被覆盖 |
| `--fleet PATH` | 指向另一份 fleet 文件（默认：脚本旁边的 `fleet.yaml`） |

`--check` 与 `--apply` 是必选的互斥对（`fleet_apply.py:850-857`）。

## 测试

`tests/test_fleet_apply.py`——110 例单测 + tempdir 集成测试，不触网、不碰真实材料。
CI（`.github/workflows/ci.yml`）跑同样两道闸：`python -m unittest discover -s tests`
与 `ruff check .`（钉在 0.16.9）。发版由用户手动触发；CI 无发版 job。
