
## Codex 深审发现（2026-11-13，luna profile 近5 commit 审核，5C/10M/7m，待修）

- [x] 为 `--apply` 增加原子写入、文件锁与变更前快照（`fleet_apply.py:541-590` `apply_material`）——消除半写/lost update 和不可回滚风险。（2026-11-14：tmp+fsync+os.replace 原子替换、`.fleet-lock` flock 防并发、`.fleet-bak` 0600 快照；ApplyMaterialTest 7 例，65 tests OK）
- [x] 校验并拒绝材料路径穿越、绝对路径越界与符号链接（`fleet_apply.py:70-89` `validate_material_path`，`load_fleet` 接线）——防止恶意或错误 YAML 写到授权目录外。（2026-11-14：拒绝相对路径/`..`/软链目标/软链祖先；MaterialPathValidationTest 6 例，33 tests OK）
- [x] 禁止含换行/标记形态的 secret 进入 env 值，或改为安全编码（`fleet_apply.py:34-52` `ensure_safe_value` + `render_value` 序列化防线 + `merge_managed_block`/`resolve` 接线）——阻断管理块注入。（2026-11-14：换行/CR/NUL/块标记一律拒绝；SecretInjectionGuardTest 5 例，38 tests OK）
- [x] 改造托管块重组策略，保证冲突和失败场景保留完整原内容（`fleet_apply.py:160-225`）——避免清空块造成旧 secret 丢失。（2026-11-14：空清单拒写、冲突变量保留块内旧值、清单收缩变量原值保留并报告；ManagedBlockTest 新增 5 例，42 tests OK）
- [x] 让 `--check` 检测重复、未闭合、错序和重置过的管理块（`fleet_apply.py:139-159` `block_structure_issues`，`check_material`/`merge_managed_block` 双向接线）——消除重置标记掩盖漂移的绕过路径。（2026-11-14：重复 BEGIN/未闭合/END 错序/空块重置均报失败，结构异常时重组拒写；BlockStructureTest 7 例，72 tests OK）
- [x] 将 Bifrost 只读检查改为真正无副作用打开，并前置 schema 校验（`fleet_apply.py:372-401` `_bifrost_connect`，`check_bifrost_key`/`check_bifrost_vk` 共用）——消除 SQLite journal/WAL 副作用。（2026-11-14：`mode=ro&immutable=1` 不碰 journal/WAL/SHM；缺文件/缺表/读失败分别精确报错，错误消息只留异常类型不泄漏细节；BifrostCheckTest 6 例，78 tests OK）
- [x] 把 litellm/hermes 只读目标路径声明进 `fleet.yaml` 并从配置读取（`fleet.yaml` `check_targets` 四键 + `fleet_apply.py:788-795` `_validate_check_targets`/`read_target`，main 全部接线）——恢复单一事实源，支持跨设备。（2026-11-14：litellm/hermes 配置、bifrost db、magpie keys 全部改从配置读取，未知键/非字符串值拒绝；测试 101 tests OK）
- [x] 将 plaintext allowlist 通用化到 hermes，并对未登记明文默认失败（`fleet_apply.py:413-483`，main 接线 `gaps.hermes_plaintext`）——关闭同型残留。（2026-11-14：hermes 未登记 api_key 明文默认失败，登记格式=配置路径；实测 ~/.hermes/config.yaml 0 错误；PlaintextScanTest 新增 5 例，47 tests OK）
- [x] 扩展明文扫描覆盖嵌套、列表裸值与键名变体（`fleet_apply.py:405-417` `_is_secret_slot` + `walk` 全字符串叶等值检测）——避免特殊结构绕过密钥检测。（2026-11-14：api[-_]?key 变体/列表裸值按属主键判定；vault 等值不分键名；实测两份真实 config 0 误报；PlaintextScanTest 新增 6 例，53 tests OK）
- [x] 在 `load_fleet` 校验 providers/consumers/materials/gaps 完整 schema（`fleet_apply.py:667-780` `_require_str`/`_require_str_list`/`_validate_consumer`/`_validate_gaps` + `load_fleet`）——把配置错误变成精确失败。（2026-11-14：vault/逐 target 必填字段/mode 八进制/gaps 各段形状/vault_no_consumer 引用完整性全部指名报错；FleetYamlSchemaStrictTest 12 例，98 tests OK）
- [x] 为所有 resolver、SQLite、JSON 错误生成脱敏消息（`fleet_apply.py:311-329` `sanitize`/`register_redaction`，`CheckFailure.add` 与全部错误打印出口接线）——保证错误输出不泄漏 secret 或敏感细节。（2026-11-14：vault 值（原始+归一化）注册进脱敏表；resolver HTTP body 不再进消息；SanitizeTest 5 例，58 tests OK）
- [x] 包装 SecretRef 解析的所有协议异常并精确校验 field（`fleet_apply.py:332-398` `resolve`/`_resolve`，字段名常量 `FIELD_API_KEY`）——防止协议变化导致崩溃或取错字段。（2026-11-14：列表/详情/data/字段逐级形态校验，TypeError/KeyError 等全部包装成 ResolveError，不再裸抛；SecretRefProtocolTest 7 例，85 tests OK）
- [x] 更新当前配置计数断言并从 `fleet.yaml` 推导（`tests/test_fleet_apply.py:177-189`）——恢复测试绿灯。（2026-11-14：断言改为 raw yaml 推导 + 硬编码现状 24 + 唯一性/gaps 交叉核对；27 tests OK）
- [x] 为 `merge_managed_block`/`check_material`/writer 增加 tempdir 集成测试（`tests/test_fleet_apply.py` `CheckMaterialIntegrationTest` 等 4 个测试类）——覆盖真实读写、冲突、权限和异常路径。（2026-11-14：apply→check 闭环、外部篡改漂移、所有权漂移、0600 归一、快照回滚、不可读文件/软链异常路径（顺带修了 check_material 两处崩溃口）；109 tests OK）
- [x] 添加依赖清单并更新安装/运行说明（`requirements.txt` + `README.md`/`README.zh.md` Prerequisites 第 0 步与 Layout 表）——消除 PyYAML 隐式依赖。（2026-11-14：PyYAML>=5.4 显式声明，两份 README 同步安装步骤）
- [ ] 更正 README 中 litellm/hermes 变量数量（`README.md:61-66`）——消除文档不实。
- [ ] 补充 persona 最低版本与空闲窗口验证证据说明（`README.md:39-45`）——让安全前提可复验。
- [ ] 添加最小 ruff/CI 配置并修复现有 9 个 lint 问题（`fleet_apply.py:17`）——建立回归防护基线。
