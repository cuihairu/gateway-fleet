#!/usr/bin/env python3
"""fleet-apply — gateway-fleet 的解析与落地工具。

把 persona 身份 llm-automation 里的密钥（SecretRef = IDENTITY/ITEM/FIELD，
经 Connect API 解析，无需主密码）落到各消费方的运行时材料，或校验现状：

    fleet-apply --check    校验（解析 + 材料 + 引用 + 明文扫描），漂移即非零退出
    fleet-apply --apply    生成/更新材料托管块（0600），块外旧值不一致则跳过并报告
    fleet-apply --apply --force   块外旧值不一致时也覆盖

设计红线：
  - 密钥值不进 fleet.yaml、不进 argv、不进日志（本工具输出只有名称与结论）；
  - 材料文件 0600，内容只经托管块（# >>> gateway-fleet … >>> … <<<）集中管理，
    块外行原样保留；
  - 只写 materials 声明的文件；bifrost / magpie / 配置文件一律只读校验。
"""
from __future__ import annotations

import argparse
import fcntl
import http.client
import json
import os
import re
import socket
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import yaml

BEGIN = "# >>> gateway-fleet managed (fleet-apply) >>>"
END = "# <<< gateway-fleet <<<"
SAFE_VALUE = re.compile(r"^[A-Za-z0-9._:/+@-]+$")
ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")
UNSAFE_SECRET = re.compile(r"[\x00\r\n]")
FIELD_API_KEY = "api_key"  # SecretRef 约定的字段名；协议侧变化在此一处收紧


class UnsafeSecretValue(ValueError):
    """secret 值含换行/控制字符或管理块标记——落地会被拆行或注入托管块。"""


def ensure_safe_value(item: str, value: str) -> str:
    """env 值安全门：换行/CR/NUL 会把一行 KEY=VALUE 拆成多行（可注入伪造行），
    管理块标记形态同理；API key 里它们都不合法，一律拒绝。"""
    if UNSAFE_SECRET.search(value):
        raise UnsafeSecretValue(f"{item}: 值含换行/控制字符（会拆行/注入），拒绝落地")
    for mark in (BEGIN, END):
        if mark in value:
            raise UnsafeSecretValue(f"{item}: 值含管理块标记形态，拒绝落地")
    return value


# ---------- 基础 ----------

def normalize(value: str) -> str:
    """剥掉一层包裹引号（systemd EnvironmentFile / dotenv 都会剥引号；
    历史导入可能把引号存进了 vault——比对前两侧同规则归一）。"""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def expand_vars(text: str, env: dict[str, str] | None = None) -> str:
    """展开 ${VAR} / ${VAR:-default}（仅用于路径/端点等非密字段）。"""
    env = os.environ if env is None else env

    def sub(m: re.Match[str]) -> str:
        name, has_default, default = m.group(1), m.group(2) is not None, m.group(3)
        val = env.get(name)
        if val:
            return val
        if has_default:
            return default or ""
        return m.group(0)

    return ENV_REF.sub(sub, text)


def expand_path(text: str) -> Path:
    return Path(os.path.expanduser(expand_vars(text)))


def validate_material_path(raw: str, name: str) -> Path:
    """材料路径必须落在授权位置内：展开后为绝对路径、无 .. 穿越、
    目标与已存在的祖先目录都不是符号链接。

    materials.path 是 fleet-apply 唯一授权的写入目标；穿越、绝对路径越界或
    软链重定向都会把 0600 密钥写到声明位置之外，所以在 load 阶段一次拒绝。
    """
    p = expand_path(raw)
    if not p.is_absolute():
        raise SystemExit(
            f"fleet.yaml: material {name}: 展开后必须是绝对路径，得到 {p}"
        )
    if ".." in p.parts:
        raise SystemExit(f"fleet.yaml: material {name}: 路径含 ..（拒绝穿越）")
    if p.is_symlink():
        raise SystemExit(f"fleet.yaml: material {name}: 目标是符号链接 {p}")
    for anc in p.parents:
        if anc.is_symlink():
            raise SystemExit(f"fleet.yaml: material {name}: 祖先目录是符号链接 {anc}")
    return p


def render_value(value: str) -> str:
    """env 行的值序列化：安全字符直写，否则双引号包裹（systemd/dotenv 同解）。

    引号包裹不改写换行——含换行/标记的值在这里是最后防线，直接拒绝。
    """
    if UNSAFE_SECRET.search(value) or BEGIN in value or END in value:
        raise UnsafeSecretValue("值含换行/控制字符或管理块标记，拒绝序列化")
    if SAFE_VALUE.match(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def parse_env_lines(text: str) -> dict[str, str]:
    """解析 env 文件的 KEY=VALUE 行（值原样返回，未剥引号）。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = ENV_LINE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def split_managed(text: str) -> tuple[dict[str, str], dict[str, str], list[str], bool]:
    """拆出托管块内变量、块外变量、块外保留行、块是否存在。"""
    lines = text.splitlines(keepends=True)
    inside: list[str] = []
    outside: list[str] = []
    in_block = False
    block_found = False
    for line in lines:
        stripped = line.strip()
        if stripped == BEGIN:
            in_block = True
            block_found = True
            continue
        if stripped == END:
            in_block = False
            continue
        (inside if in_block else outside).append(line)
    block_vars = parse_env_lines("".join(inside))
    outside_vars = parse_env_lines("".join(outside))
    return block_vars, outside_vars, outside, block_found


def block_structure_issues(text: str) -> list[str]:
    """托管块结构体检：重复 BEGIN、未闭合 BEGIN、END 错序。

    这些异常会让"块内/块外"的归属变得不明确——检查要报，重组要拒，
    否则漂移可能被伪造的块标记掩盖。
    """
    issues: list[str] = []
    seen_begin = 0
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == BEGIN:
            seen_begin += 1
            if seen_begin > 1:
                issues.append(f"第 {seen_begin} 处重复出现 BEGIN")
            in_block = True
        elif stripped == END:
            if not in_block:
                issues.append("END 出现在托管块之外（错序）")
            in_block = False
    if in_block:
        issues.append("BEGIN 未闭合（缺 END）")
    return issues


def render_block(vars_in_order: dict[str, str]) -> str:
    body = "".join(f"{k}={render_value(v)}\n" for k, v in vars_in_order.items())
    return f"{BEGIN}\n{body}{END}\n"


def merge_managed_block(
    text: str, wanted: dict[str, str], force: bool
) -> tuple[str, list[str], list[str]]:
    """把 wanted 写入托管块。返回 (新文本, 变更说明, 冲突说明)。

    - 块外同名行：归一值一致 → 移入块（所有权收口）；不一致 → 冲突，除非 force；
    - 已在块内且值一致 → 不动；
    - 冲突变量若已有托管旧值、或块内变量不在本次清单 → 原值保留在块内不删，
      报告提请人工处理——任何重组场景都不得清空块造成旧 secret 丢失。
    """
    if not wanted:
        # 空清单重写会把整块清空（历史 secret 全部丢失）——拒绝，提请检查 fleet.yaml。
        raise ValueError("wanted 为空：拒绝重写托管块（会丢失块内旧内容）")
    issues = block_structure_issues(text)
    if issues:
        # 块归属不明时重组只会产出更糟的文件——先人工修复。
        raise ValueError("原文托管块结构异常：" + "；".join(issues) + "（人工修复后再 --apply）")
    for k, v in wanted.items():
        ensure_safe_value(k, v)
    block_vars, outside_vars, outside_lines, _ = split_managed(text)
    changes: list[str] = []
    conflicts: list[str] = []

    kept_outside: list[str] = []
    outside_set = set(outside_vars)
    for line in outside_lines:
        m = ENV_LINE.match(line.strip())
        name = m.group(1) if m else None
        if name in wanted and name in outside_set:
            if normalize(outside_vars[name]) == normalize(wanted[name]):
                changes.append(f"{name}: 块外同值行移入托管块")
                continue  # 丢弃原行，由块接管
            if not force:
                conflicts.append(
                    f"{name}: 块外现值与 persona 不一致（跳过；--force 覆盖）"
                )
                kept_outside.append(line)
                continue
            changes.append(f"{name}: 覆盖块外不同值（--force）")
            continue
        kept_outside.append(line)

    conflict_names = {c.split(":", 1)[0] for c in conflicts}
    # 保底重组：块内旧变量不因清单收缩/冲突而消失
    final_wanted: dict[str, str] = {}
    for k, v in block_vars.items():
        if k in wanted:
            if k in conflict_names:
                final_wanted[k] = v  # 旧托管值留块内，块外新值继续生效
                conflicts.append(
                    f"{k}: 块内旧值保留待人工处理（块外另有不同值，未写入 persona 值）"
                )
            continue  # 在清单且无冲突 → 由 wanted 决定
        final_wanted[k] = v
        changes.append(f"{k}: 不在本次清单，块内旧值保留")
    for k, v in wanted.items():
        if k not in conflict_names:
            final_wanted[k] = v
    # 块内已一致的变量不产生变更说明
    for k, v in final_wanted.items():
        if k in block_vars and normalize(block_vars[k]) == normalize(v):
            continue
        if not any(c.startswith(k + ":") for c in changes):
            changes.append(f"{k}: 写入托管块" if k not in block_vars else f"{k}: 更新托管块值")

    new_text = "".join(kept_outside)
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"
    new_text += render_block(final_wanted)
    return new_text, changes, conflicts


# ---------- Connect API 解析 ----------

class UnixHTTPConnection(http.client.HTTPConnection):
    """经 unix domain socket 的 HTTP 连接（persona connect serve --unix-socket）。"""

    def __init__(self, path: str, timeout: float = 10.0):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


class ResolveError(RuntimeError):
    pass


class ConnectResolver:
    def __init__(self, socket_path: str, token: str):
        self.socket_path = socket_path
        self.token = token

    def _get(self, path: str) -> Any:
        conn = UnixHTTPConnection(self.socket_path)
        try:
            conn.request(
                "GET", path, headers={"Authorization": f"Bearer {self.token}"}
            )
            resp = conn.getresponse()
            body = resp.read().decode("utf-8", "replace")
        except OSError as e:
            raise ResolveError(
                f"connect serve 不可达（{self.socket_path}）：{e}\n"
                "  → 先启动：persona connect serve --unix-socket（前台）"
                " 或 systemctl --user start persona-connect（systemd 样例见 persona "
                "docs/EXTERNAL_INTEGRATION.md Step 2）"
            ) from e
        finally:
            conn.close()
        if resp.status == 401:
            raise ResolveError("connect token 被拒（401）——检查 PERSONA_CONNECT_TOKEN")
        if resp.status == 503:
            raise ResolveError("保险库未解锁（503 vault_locked）——serve 正在解锁或解锁失败")
        if resp.status != 200:
            # body 可能回显条目详情（含密钥字段）——状态码足够定位，内容不进消息
            raise ResolveError(f"GET {path} → HTTP {resp.status}（body 已省略）")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise ResolveError(f"GET {path} 非 JSON（body 已省略）：{type(e).__name__}") from e
        if not payload.get("ok"):
            raise ResolveError(f"GET {path} 业务错误 HTTP {resp.status}（body 已省略）")
        return payload.get("data")

    def resolve(self, item: str) -> str:
        """解析 llm-automation/<item>/api_key → 密钥值（内存态，不落盘不打印）。

        协议形态（列表/详情/字段名）任何变化都降级成 ResolveError——
        绝不把裸 TypeError/KeyError 抛到调用方。
        """
        try:
            return self._resolve(item)
        except ResolveError:
            raise
        except (TypeError, AttributeError, KeyError, IndexError, ValueError) as e:
            raise ResolveError(
                f"{item}: 协议解析异常（{type(e).__name__}）——connect 响应形态可能已变化"
            ) from e

    def _resolve(self, item: str) -> str:
        items = self._get("/api/v1/connect/items")
        if not isinstance(items, list):
            raise ResolveError(
                "条目列表形态异常（期望数组，得到 "
                f"{type(items).__name__}）——connect 协议可能已变化"
            )
        entry = None
        for it in items:
            if not isinstance(it, dict):
                continue
            if item in (it.get("title"), it.get("name")):
                entry = it
                break
        if entry is None:
            raise ResolveError(f"scope 内找不到条目 {item!r}")
        item_id = entry.get("id")
        if item_id in (None, "") or not isinstance(item_id, (str, int)):
            raise ResolveError(f"条目 {item!r} 缺少 id（协议形态异常）")
        detail = self._get(f"/api/v1/connect/items/{item_id}")
        if not isinstance(detail, dict):
            raise ResolveError(f"{item}: 详情非对象（协议形态异常）")
        data = detail.get("data")
        if not isinstance(data, dict):
            raise ResolveError(f"{item}: 详情缺 data 对象（协议形态异常）")
        variant = data.get("ApiKey")
        used = "ApiKey"
        if not isinstance(variant, dict):
            variant, used = data, "data"
        value = variant.get(FIELD_API_KEY)
        if not isinstance(value, str) or not value:
            raise ResolveError(
                f"{item}: {used}.{FIELD_API_KEY} 缺失或为空"
                f"（字段形态：{sorted(map(str, data))}）"
            )
        raw = value
        value = normalize(value)
        # 原始与归一化形态都登记——错误输出（yaml 片段等）两侧都可能出现
        register_redaction(raw)
        register_redaction(value)
        try:
            ensure_safe_value(item, value)
        except UnsafeSecretValue as e:
            raise ResolveError(str(e)) from e
        return value


# ---------- 输出脱敏 ----------

_redactions: list[str] = []


def register_redaction(value: str) -> None:
    """登记已知 secret 值；之后所有错误输出先经 sanitize 过滤。"""
    if value and value not in _redactions:
        _redactions.append(value)


def sanitize(msg: str) -> str:
    """把消息里出现的 vault 值替换为 ***（按长度降序，防子串遮蔽）。"""
    for v in sorted(_redactions, key=len, reverse=True):
        if v in msg:
            msg = msg.replace(v, "***")
    return msg


# ---------- 各类消费方校验 ----------

class CheckFailure:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def add(self, msg: str) -> None:
        self.errors.append(sanitize(msg))


def check_material(
    name: str, spec: dict[str, Any], wanted: dict[str, str], fail: CheckFailure
) -> None:
    path = expand_path(spec["path"])
    if not path.exists():
        fail.add(f"material {name}: 文件不存在 {path}")
        return
    if path.is_symlink():
        fail.add(f"material {name}: 目标是符号链接 {path}（拒绝校验）")
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        fail.add(f"material {name}: 读取失败（{type(e).__name__}）")
        return
    issues = block_structure_issues(text)
    if issues:
        for issue in issues:
            fail.add(f"material {name}: 托管块结构异常：{issue}（人工修复后再 --apply）")
        return  # 块归属不明时继续拆只会产出误导性结果
    block_vars, outside_vars, _, present = split_managed(text)
    if not present:
        fail.add(f"material {name}: 托管块缺失（跑 --apply）")
        return
    if not block_vars and wanted:
        fail.add(f"material {name}: 托管块为空（疑似被重置；跑 --apply 恢复）")
    for var, value in wanted.items():
        if var not in block_vars:
            fail.add(f"material {name}: 托管块缺 {var}")
        elif normalize(block_vars[var]) != value:
            fail.add(f"material {name}: {var} 与 persona 值不一致（漂移）")
        if var in outside_vars:
            fail.add(f"material {name}: {var} 在块外另有定义（所有权漂移）")


def _bifrost_connect(db_path: str, table: str, label: str, fail: CheckFailure):
    """真正无副作用打开 bifrost 库并前置校验 schema。

    `mode=ro&immutable=1`：SQLite 完全不碰 journal/WAL/SHM（只读校验的
    读端不该有写副作用）。代价是忽略未落库的 WAL 内容——对"值已轮换但
    进程还在写"的窗口会报假漂移，属可接受取舍（重跑即可）。
    不满足前置条件时已写入 fail 并返回 None。
    """
    p = expand_path(db_path)
    if not p.is_file():
        fail.add(f"{label}: 库文件不存在 {p}")
        return None
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error as e:
        fail.add(f"{label}: 打开库失败（{type(e).__name__}）")
        return None
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    except sqlite3.Error as e:
        fail.add(f"{label}: 打开库失败（{type(e).__name__}）")
        conn.close()
        return None
    if not exists:
        fail.add(f"{label}: schema 不符（缺表 {table}）")
        conn.close()
        return None
    return conn


def check_bifrost_key(name: str, db_path: str, value: str, fail: CheckFailure) -> None:
    label = f"bifrost-key {name}"
    conn = _bifrost_connect(db_path, "config_keys", label, fail)
    if conn is None:
        return
    try:
        rows = conn.execute(
            "SELECT value FROM config_keys WHERE name=?", (name,)
        ).fetchall()
    except sqlite3.Error as e:
        fail.add(f"{label}: 读取失败（{type(e).__name__}）")
        return
    finally:
        conn.close()
    if not rows:
        fail.add(f"{label}: 不存在")
    elif normalize(rows[0][0] or "") != value:
        fail.add(f"{label}: 值与 persona 不一致（bifrost 侧已轮换？）")


def check_bifrost_vk(name: str, db_path: str, value: str, fail: CheckFailure) -> None:
    label = f"bifrost-vk {name}"
    conn = _bifrost_connect(db_path, "governance_virtual_keys", label, fail)
    if conn is None:
        return
    try:
        rows = conn.execute(
            "SELECT value FROM governance_virtual_keys WHERE name=?", (name,)
        ).fetchall()
    except sqlite3.Error as e:
        fail.add(f"{label}: 读取失败（{type(e).__name__}）")
        return
    finally:
        conn.close()
    if not rows:
        fail.add(f"{label}: 不存在")
    elif normalize(rows[0][0] or "") != value:
        fail.add(f"{label}: 值与 persona 不一致")


def check_magpie_caller(name: str, path: Path, value: str, fail: CheckFailure) -> None:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        fail.add(f"magpie-caller {name}: 读取失败 {e}")
        return
    for e in entries:
        if isinstance(e, dict) and e.get("name") == name:
            if normalize(e.get("secret", "")) != value:
                fail.add(f"magpie-caller {name}: secret 与 persona 不一致")
            return
    fail.add(f"magpie-caller {name}: 不存在")


def yaml_get_path(node: Any, path: str) -> Any:
    """取 `a.b[0].c` 形态路径；取不到抛 KeyError。"""
    cur = node
    for seg in re.findall(r"[^\[\].]+|\[\d+\]", path):
        if seg.startswith("["):
            cur = cur[int(seg[1:-1])]
        else:
            cur = cur[seg]
    return cur


def check_hermes_refs(
    paths: list[str], ref: str, file_path: Path, fail: CheckFailure
) -> None:
    try:
        cfg = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        fail.add(f"hermes-config: 解析失败 {e}")
        return
    for p in paths:
        try:
            got = yaml_get_path(cfg, p)
        except (KeyError, IndexError, TypeError):
            fail.add(f"hermes-config: 路径不存在 {p}")
            continue
        if got != ref:
            fail.add(f"hermes-config: {p} 不是 {ref} 引用（退回明文？）")


API_KEY_NAME = re.compile(r"(?i)^(?:[a-z0-9]+[._-])?api[-_]?keys?$")


def _is_secret_slot(path: str) -> bool:
    """路径末段（剥掉 [N] 索引）是否 api_key 形键名——列表裸值按属主键判定。
    覆盖 api_key / api-key / apikey / apiKeys / x.api_key / api_keys 等变体。"""
    base = re.sub(r"(\[\d+\])+$", "", path)
    return bool(API_KEY_NAME.match(base.rsplit(".", 1)[-1]))


def scan_plaintext_leaks(
    kind: str,
    file_path: Path,
    vault_values: dict[str, str],
    orphan_allow: dict[str, int],
    fail: CheckFailure,
) -> None:
    """配置文件明文扫描：任何 api_key 明文 == vault 值 → 失败；
    孤儿明文按登记数核对——litellm 按 model（gaps.litellm_plaintext_orphans），
    其余 kind（hermes 等）按配置路径（gaps.hermes_plaintext 等），
    未登记的明文默认失败（防同型残留绕过）。"""
    try:
        cfg = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        fail.add(f"{kind}-config: 解析失败 {e}")
        return
    vault_norm = {normalize(v) for v in vault_values.values()}
    plaintext_by_model: dict[str, int] = {}
    plaintext_by_path: dict[str, int] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            if node.startswith(("${", "os.environ/")):
                return
            # 键名变体与列表裸值都按属主键判定 secret 槽位；
            # vault 值等值则不分键名一律失败（任意嵌套深度）。
            if _is_secret_slot(path):
                if kind == "litellm":
                    model = "unknown"
                    m = re.match(r".*model_list\[(\d+)\]", path)
                    if m:
                        try:
                            model = cfg["model_list"][int(m.group(1))].get("model_name", "unknown")
                        except (IndexError, KeyError, AttributeError):
                            model = "unknown"
                    plaintext_by_model[model] = plaintext_by_model.get(model, 0) + 1
                else:
                    plaintext_by_path[path] = plaintext_by_path.get(path, 0) + 1
            if normalize(node) in vault_norm:
                fail.add(f"{kind}-config: {path} 明文存放 vault 值（应改引用）")

    walk(cfg, kind)
    if kind == "litellm":
        for model, count in plaintext_by_model.items():
            allowed = orphan_allow.get(model, 0)
            if count > allowed:
                fail.add(
                    f"litellm-config: model {model} 明文行 {count} > 允许的孤儿数 {allowed}"
                )
        for model, allowed in orphan_allow.items():
            got = plaintext_by_model.get(model, 0)
            if got < allowed:
                fail.add(
                    f"litellm-config: model {model} 明文行 {got} < 登记孤儿数 {allowed}（补录后请收紧 gaps）"
                )
    else:
        # 其余只读配置：未登记的 api_key 明文默认失败（登记格式 = 配置路径）
        for path, count in plaintext_by_path.items():
            allowed = orphan_allow.get(path, 0)
            if count > allowed:
                fail.add(
                    f"{kind}-config: {path} 未登记明文 {count} 处（入库后请改引用，"
                    f"或登记到 gaps 再收紧）"
                )
        for path, allowed in orphan_allow.items():
            got = plaintext_by_path.get(path, 0)
            if got < allowed:
                fail.add(
                    f"{kind}-config: {path} 明文 {got} < 登记数 {allowed}（补录后请收紧 gaps）"
                )


# ---------- 材料写入（锁 + 快照 + 原子替换） ----------

LOCK_RETRY = 50  # × 0.1s——并发实例争锁最多等 5s


def apply_material(
    name: str, spec: dict[str, Any], wanted: dict[str, str], force: bool
) -> tuple[list[str], list[str]]:
    """读-改-写单个材料，返回 (变更说明, 冲突说明)。

    - 文件锁：同目录 sidecar `.fleet-lock`（flock 非阻塞重试）防并发实例 lost update；
    - 变更前快照：`.fleet-bak`（同 mode）保留整份旧内容，可回滚；
    - 原子写入：临时文件 + fsync + os.replace——读方永远见不到半写文件。
    """
    path = validate_material_path(spec["path"], name)  # 用前再校验（防 TOCTOU）
    mode = int(spec.get("mode", "0600"), 8)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".fleet-lock")
    with open(lock_path, "a", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        for _ in range(LOCK_RETRY):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError(f"文件锁等待超时 {lock_path}（另一实例在运行？）")
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        # 值不安全/空清单 → ValueError；锁竞争 → RuntimeError（均由调用方收口）
        new, changes, conflicts = merge_managed_block(old, wanted, force)
        if new != old:
            if old:
                snap = path.with_name(path.name + ".fleet-bak")
                snap.write_text(old, encoding="utf-8")
                os.chmod(snap, mode)
            tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
            with open(tmp, "w", encoding="utf-8") as t:
                t.write(new)
                t.flush()
                os.fsync(t.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, path)
            try:  # 目录项落盘（尽力而为）
                dfd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            except OSError:
                pass
        if path.exists():
            os.chmod(path, mode)
        return changes, conflicts


# ---------- 主流程 ----------

_CONSUMER_TARGETS = {
    "litellm-env",
    "hermes-env",
    "hermes-config",
    "bifrost-key",
    "bifrost-vk",
    "magpie-caller",
}


def _require_str(v: Any, what: str) -> str:
    if not isinstance(v, str) or not v:
        raise SystemExit(f"fleet.yaml: {what} 必须是非空字符串")
    return v


def _require_str_list(v: Any, what: str) -> list[str]:
    if (
        not isinstance(v, list)
        or not v
        or any(not isinstance(x, str) or not x for x in v)
    ):
        raise SystemExit(f"fleet.yaml: {what} 必须是非空字符串列表")
    return v


def _validate_consumer(item: str, c: Any) -> None:
    if not isinstance(c, dict):
        raise SystemExit(f"fleet.yaml: {item} consumer 必须是映射")
    t = c.get("target")
    if t not in _CONSUMER_TARGETS:
        raise SystemExit(f"fleet.yaml: {item} 未知 target {t!r}")
    if t in ("litellm-env", "hermes-env"):
        _require_str_list(c.get("vars"), f"{item} target {t} 的 vars")
    elif t == "hermes-config":
        _require_str(c.get("ref"), f"{item} target hermes-config 的 ref")
        _require_str_list(c.get("paths"), f"{item} target hermes-config 的 paths")
    else:  # bifrost-key / bifrost-vk / magpie-caller
        _require_str(c.get("name"), f"{item} target {t} 的 name")
        opt = "db" if t.startswith("bifrost") else "file"
        if c.get(opt) is not None:
            _require_str(c.get(opt), f"{item} target {t} 的 {opt}")


def _validate_gaps(cfg: dict[str, Any], names: list[str]) -> None:
    gaps = cfg.get("gaps") or {}
    if not isinstance(gaps, dict):
        raise SystemExit("fleet.yaml: gaps 必须是映射")
    for e in gaps.get("litellm_plaintext_orphans", []):
        if not isinstance(e, dict) or not isinstance(e.get("model"), str):
            raise SystemExit(
                "fleet.yaml: gaps.litellm_plaintext_orphans[] 需要 {model, count}"
            )
        if not isinstance(e.get("count"), int) or e["count"] < 0:
            raise SystemExit(
                f"fleet.yaml: gaps.litellm_plaintext_orphans[{e.get('model')}] "
                "count 需非负整数"
            )
    for e in gaps.get("hermes_plaintext", []):
        if isinstance(e, dict):
            _require_str(e.get("path"), "gaps.hermes_plaintext[].path")
            cnt = e.get("count", 1)
            if not isinstance(cnt, int) or cnt < 0:
                raise SystemExit("fleet.yaml: gaps.hermes_plaintext[].count 需非负整数")
        elif not isinstance(e, str) or not e:
            raise SystemExit(
                "fleet.yaml: gaps.hermes_plaintext[] 需路径字符串或 {path, count}"
            )
    for key in ("litellm_env_unmanaged", "vault_no_consumer"):
        lst = gaps.get(key, [])
        if not isinstance(lst, list) or any(
            not isinstance(x, str) or not x for x in lst
        ):
            raise SystemExit(f"fleet.yaml: gaps.{key} 必须是字符串列表")
    known = gaps.get("known_quirks", [])
    if not isinstance(known, list) or any(not isinstance(x, str) for x in known):
        raise SystemExit("fleet.yaml: gaps.known_quirks 必须是字符串列表")
    for n in gaps.get("vault_no_consumer", []):
        if n not in names:
            raise SystemExit(f"fleet.yaml: gaps.vault_no_consumer 引用不存在的 item {n!r}")

def _validate_check_targets(cfg: dict[str, Any]) -> dict[str, str]:
    targets = cfg.get("check_targets") or {}
    if not isinstance(targets, dict):
        raise SystemExit("fleet.yaml: check_targets 必须是映射")
    allowed = {
        "litellm_config",
        "hermes_config",
        "bifrost_db",
        "magpie_caller_keys",
    }
    for k, v in targets.items():
        if k not in allowed:
            raise SystemExit(f"fleet.yaml: check_targets 未知键 {k!r}")
        _require_str(v, f"check_targets.{k}")
    return targets


def load_fleet(path: Path) -> dict[str, Any]:
    """解析并完整校验 fleet.yaml——把配置错误变成精确失败（指名道姓）。"""
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or cfg.get("version") != 1:
        raise SystemExit("fleet.yaml: 需要 version: 1 的映射")

    vault = cfg.get("vault")
    if not isinstance(vault, dict):
        raise SystemExit("fleet.yaml: 缺 vault 映射")
    _require_str(vault.get("identity"), "vault.identity")
    _require_str(vault.get("endpoint"), "vault.endpoint")
    _require_str(vault.get("token_env") or "PERSONA_CONNECT_TOKEN", "vault.token_env")

    items = cfg.get("providers")
    if not isinstance(items, list) or not items:
        raise SystemExit("fleet.yaml: providers 必须是非空列表")
    names: list[str] = []
    for p in items:
        if not isinstance(p, dict):
            raise SystemExit("fleet.yaml: providers[] 必须是映射")
        names.append(_require_str(p.get("item"), "providers[].item"))
        consumers = p.get("consumers") or []
        if not isinstance(consumers, list):
            raise SystemExit(f"fleet.yaml: {p['item']} consumers 必须是列表")
        for c in consumers:
            _validate_consumer(p["item"], c)
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise SystemExit(f"fleet.yaml: item 重复 {sorted(dup)}")

    mats = cfg.get("materials") or {}
    if not isinstance(mats, dict) or not mats:
        raise SystemExit("fleet.yaml: materials 必须是非空映射")
    for name, spec in mats.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("path"), str):
            raise SystemExit(f"fleet.yaml: material {name}: 需要 path 字符串")
        validate_material_path(spec["path"], name)
        mode = spec.get("mode", "0600")
        try:
            int(mode, 8)
        except (TypeError, ValueError):
            raise SystemExit(
                f"fleet.yaml: material {name}: mode 必须是八进制字符串"
            ) from None

    _validate_gaps(cfg, names)
    _validate_check_targets(cfg)
    return cfg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="gateway-fleet 密钥落地/校验")
    ap.add_argument("--fleet", default=str(Path(__file__).with_name("fleet.yaml")))
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="校验现状（只读）")
    mode.add_argument("--apply", action="store_true", help="生成/更新材料托管块")
    ap.add_argument("--force", action="store_true", help="覆盖块外不一致的旧值")
    args = ap.parse_args(argv)

    cfg = load_fleet(Path(args.fleet))
    vault = cfg["vault"]
    identity = vault["identity"]
    endpoint = expand_vars(vault["endpoint"])
    if not endpoint.startswith("unix://"):
        raise SystemExit(f"vault.endpoint 目前只支持 unix://，得到 {endpoint}")
    socket_path = endpoint[len("unix://"):]
    token = os.environ.get(vault.get("token_env", "PERSONA_CONNECT_TOKEN"), "")
    if not token:
        raise SystemExit(
            f"缺少 {vault.get('token_env', 'PERSONA_CONNECT_TOKEN')} 环境变量（token 不落文件）"
        )

    print(f"[fleet] identity={identity} endpoint={socket_path}")
    resolver = ConnectResolver(socket_path, token)

    # 解析全部 item（值只在内存）
    values: dict[str, str] = {}
    try:
        for p in cfg["providers"]:
            values[p["item"]] = resolver.resolve(p["item"])
    except ResolveError as e:
        print(f"[fleet] FAIL resolve: {sanitize(str(e))}", file=sys.stderr)
        return 1
    print(f"[fleet] resolve: {len(values)}/{len(cfg['providers'])} ok")

    # 组装每个 material 的托管变量
    mats = cfg.get("materials", {})
    wanted: dict[str, dict[str, str]] = {name: {} for name in mats}
    for p in cfg["providers"]:
        val = values[p["item"]]
        for c in p.get("consumers") or []:
            if c["target"] in wanted:
                for var in c.get("vars", []):
                    wanted[c["target"]][var] = val

    fail = CheckFailure()
    targets = cfg.get("check_targets") or {}

    def read_target(key: str, default: str) -> str:
        """只读校验目标：fleet.yaml check_targets 优先，缺省退回内置默认。"""
        return targets.get(key) or default

    if args.apply:
        for name, spec in mats.items():
            try:
                changes, conflicts = apply_material(
                    name, spec, wanted[name], args.force
                )
            except (ValueError, RuntimeError) as e:  # 不安全值 / 空清单 / 锁超时
                print(f"[apply] {name}: FAIL {sanitize(str(e))}", file=sys.stderr)
                fail.add(f"{name}: {e}")
                continue
            for c in changes:
                print(f"[apply] {name}: {c}")
            for c in conflicts:
                print(f"[apply] {name}: CONFLICT {c}")
                fail.add(f"{name}: {c}")
            if not changes and not conflicts:
                print(f"[apply] {name}: 已是最新")
        # apply 后统一走 check 收口
        print("[fleet] apply 完成，转入校验…")

    # ---- check（apply 之后也执行）----
    print(f"[check] schema: {len(cfg['providers'])} providers, {len(mats)} materials")

    for name, spec in mats.items():
        check_material(name, spec, wanted[name], fail)

    hermes_cfg_path = None
    for p in cfg["providers"]:
        val = values[p["item"]]
        for c in p.get("consumers") or []:
            t, cname = c["target"], c.get("name")
            if t == "bifrost-key":
                check_bifrost_key(
                    cname,
                    c.get("db") or read_target(
                        "bifrost_db", "~/.local/share/bifrost/data/config.db"
                    ),
                    val, fail,
                )
            elif t == "bifrost-vk":
                check_bifrost_vk(
                    cname,
                    c.get("db") or read_target(
                        "bifrost_db", "~/.local/share/bifrost/data/config.db"
                    ),
                    val, fail,
                )
            elif t == "magpie-caller":
                check_magpie_caller(
                    cname,
                    expand_path(
                        c.get("file") or read_target(
                            "magpie_caller_keys", "~/.config/magpie/caller-keys.json"
                        )
                    ),
                    val, fail,
                )
            elif t == "hermes-config":
                hermes_cfg_path = expand_path(
                    c.get("file")
                    or read_target("hermes_config", "~/.hermes/config.yaml")
                )
                check_hermes_refs(c["paths"], c["ref"], hermes_cfg_path, fail)

    # 明文泄漏扫描（两类配置；目标路径同源 check_targets）
    gaps = cfg.get("gaps") or {}
    allow = {
        e["model"]: e["count"]
        for e in gaps.get("litellm_plaintext_orphans", [])
    }
    scan_plaintext_leaks(
        "litellm",
        expand_path(read_target("litellm_config", "~/.local/share/litellm/config.yaml")),
        values, allow, fail,
    )
    if hermes_cfg_path is None:
        hermes_cfg_path = expand_path(
            read_target("hermes_config", "~/.hermes/config.yaml")
        )
    # hermes 登记格式：路径字符串（每条记 1 处）或 {path, count}
    hermes_allow: dict[str, int] = {}
    for e in gaps.get("hermes_plaintext", []):
        if isinstance(e, str):
            hermes_allow[e] = hermes_allow.get(e, 0) + 1
        elif isinstance(e, dict) and isinstance(e.get("path"), str):
            hermes_allow[e["path"]] = hermes_allow.get(e["path"], 0) + int(
                e.get("count", 1)
            )
    scan_plaintext_leaks("hermes", hermes_cfg_path, values, hermes_allow, fail)

    if fail.errors:
        print(f"[check] FAIL ({len(fail.errors)}):")
        for e in fail.errors:
            print(f"  - {sanitize(e)}")
        return 1
    print("[check] OK — 材料、引用、明文扫描全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
