# 配置键与参数

下表每个键都在当前 `fleet.yaml` 里真实存在——没有前瞻性条目。计数按审计时
重算自文件本身。

## 顶层

| 键 | 必填 | 含义 |
| --- | --- | --- |
| `version` | 是 | 模式版本，当前 `1` |
| `vault` | 是 | 密钥来源与解析方式 |
| `materials` | 是 | 可写文件及其托管块的写入方式 |
| `check_targets` | 否 | 只读校验路径（覆盖内置默认值；单条消费方可再用 `db` / `file` 覆盖） |
| `providers` | 是 | SecretRef → 消费方的接线（当前 24 条） |
| `gaps` | 否 | 明文扫描与 env 覆盖审计的登记例外 |

## `vault`

| 键 | 含义 |
| --- | --- |
| `identity` | 持有密钥的 persona 身份——`llm-automation` |
| `resolver` | `connect`：unix socket 的 Connect API（不经主密码） |
| `endpoint` | unix socket，支持 env 展开（`${XDG_RUNTIME_DIR:-/run/user/1000}`） |
| `token_env` | 存放作用域 `pconn_` token 的环境变量名——token 绝不出现在文件里 |
| `normalize` | `strip-quotes`：比对与落地前剥掉 `litellm-master-key` 的导入瑕疵引号 |

## `materials`

每个可写文件一条：

| 键 | 含义 |
| --- | --- |
| `path` | 绝对路径或 `~` 路径；相对路径、`..` 段、符号链接与带软链祖先均被拒绝 |
| `mode` | 新建时写入的八进制权限字符串（材料为 `0600`） |
| `restart_hint` | 变化时提示；加载配置时重读的消费者写 `null` |

当前材料：`litellm-env` → `~/.local/share/litellm/env.systemd`，
`hermes-env` → `~/.hermes/.env`。

## 消费方 target

| target | 必填键 | 作用 |
| --- | --- | --- |
| `litellm-env` | `vars` | 把同名变量写入该材料的托管块 |
| `hermes-env` | `vars` | 同上 |
| `hermes-config` | `ref`、`paths` | 校验每个 yaml 路径仍带 `${…}` 引用而非明文 |
| `bifrost-key` | `name` | 只读：`config_keys` 中同名行的值等于 persona 值 |
| `bifrost-vk` | `name` | 只读：`governance_virtual_keys` 中同名行 |
| `magpie-caller` | `name` | 只读：`caller-keys.json` 中该 caller 的密钥 |

当前文件的 target 用量：`litellm-env` 18、`bifrost-key` 17、`hermes-env` 5、
`hermes-config` 3、`magpie-caller` 3、`bifrost-vk` 1。

## `gaps`

| 键 | 形态 | 含义 |
| --- | --- | --- |
| `litellm_plaintext_orphans` | `{model, count}` 列表 | vault 无对应 key 的明文模型——唯一允许的 litellm 明文，按模型计数 |
| `hermes_plaintext` | `{path, count}` 列表 | 允许的 hermes 明文路径；未登记的明文默认失败 |
| `litellm_env_unmanaged` | 变量名列表 | `env.systemd` 中无 vault 覆盖的变量 |
| `vault_no_consumer` | 条目 id 列表 | 无消费方的 vault 条目（备用键） |
| `known_quirks` | 字符串列表 | 自由文本备注（如带引号的 `litellm-master-key`） |

## 覆盖数字

| 材料 | vault 覆盖的变量数 |
| --- | --- |
| `litellm-env` | 20 |
| `hermes-env` | 5 —— `ARK_API_KEY`、`BIFROST_VK_MAIN`、`GLM_API_KEY`、`LITELLM_MASTER_KEY`、`XIAOMI_API_KEY` |

## CLI 参数

| 参数 | 含义 |
| --- | --- |
| `--check` | 只校验（只读）；与 `--apply` 互斥，二者必选其一 |
| `--apply` | 写托管块（0600），随后跑同一遍校验 |
| `--force` | 连块外不一致的旧值一并覆盖 |
| `--fleet PATH` | 使用另一份 fleet 文件（默认：脚本旁边的 `fleet.yaml`） |

任何漂移都会非零退出；输出只有名称与结论。
