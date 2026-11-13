
## Codex 深审发现（2026-11-13，luna profile 近5 commit 审核，5C/10M/7m，待修）

- [ ] 为 `--apply` 增加原子写入、文件锁与变更前快照（`fleet_apply.py:471-480`）——消除半写/lost update 和不可回滚风险。
- [ ] 校验并拒绝材料路径穿越、绝对路径越界与符号链接（`fleet_apply.py:66-69`）——防止恶意或错误 YAML 写到授权目录外。
- [ ] 禁止含换行/标记形态的 secret 进入 env 值，或改为安全编码（`fleet_apply.py:70-82`）——阻断管理块注入。
- [ ] 改造托管块重组策略，保证冲突和失败场景保留完整原内容（`fleet_apply.py:123-159`）——避免清空块造成旧 secret 丢失。
- [ ] 让 `--check` 检测重复、未闭合、错序和重置过的管理块（`fleet_apply.py:88-107`）——消除重置标记掩盖漂移的绕过路径。
- [ ] 将 Bifrost 只读检查改为真正无副作用打开，并前置 schema 校验（`fleet_apply.py:269-296`）——消除 SQLite journal/WAL 副作用。
- [ ] 把 litellm/hermes 只读目标路径声明进 `fleet.yaml` 并从配置读取（`fleet_apply.py:521-531`）——恢复单一事实源，支持跨设备。
- [ ] 将 plaintext allowlist 通用化到 hermes，并对未登记明文默认失败（`fleet_apply.py:342-394`）——关闭同型残留。
- [ ] 扩展明文扫描覆盖嵌套、列表裸值与键名变体（`fleet_apply.py:359-379`）——避免特殊结构绕过密钥检测。
- [ ] 在 `load_fleet` 校验 providers/consumers/materials/gaps 完整 schema（`fleet_apply.py:399-421`）——把配置错误变成精确失败。
- [ ] 为所有 resolver、SQLite、JSON 错误生成脱敏消息（`fleet_apply.py:229-235`、`269-303`）——保证错误输出不泄漏 secret 或敏感细节。
- [ ] 包装 SecretRef 解析的所有协议异常并精确校验 field（`fleet_apply.py:219-235`）——防止协议变化导致崩溃或取错字段。
- [ ] 更新当前配置计数断言并从 `fleet.yaml` 推导（`tests/test_fleet_apply.py:177-181`）——恢复测试绿灯。
- [ ] 为 `merge_managed_block`/`check_material`/writer 增加 tempdir 集成测试（`fleet_apply.py:115-160`）——覆盖真实读写、冲突、权限和异常路径。
- [ ] 添加依赖清单并更新安装/运行说明（`README.md:47-54`）——消除 PyYAML 隐式依赖。
- [ ] 更正 README 中 litellm/hermes 变量数量（`README.md:61-66`）——消除文档不实。
- [ ] 补充 persona 最低版本与空闲窗口验证证据说明（`README.md:39-45`）——让安全前提可复验。
- [ ] 添加最小 ruff/CI 配置并修复现有 9 个 lint 问题（`fleet_apply.py:17`）——建立回归防护基线。
