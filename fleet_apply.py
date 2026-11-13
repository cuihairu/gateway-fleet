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
import json
import os
import re
import socket
import sqlite3
import sys
import http.client
from pathlib import Path
from typing import Any

import yaml

BEGIN = "# >>> gateway-fleet managed (fleet-apply) >>>"
END = "# <<< gateway-fleet <<<"
SAFE_VALUE = re.compile(r"^[A-Za-z0-9._:/+@-]+$")
ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")
UNSAFE_SECRET = re.compile(r"[\x00\r\n]")


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

    def connect(self) -> None:  # noqa: D401 - http.client 契约
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
            raise ResolveError(f"GET {path} → HTTP {resp.status}: {body[:200]}")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise ResolveError(f"GET {path} 非 JSON：{body[:200]}") from e
        if not payload.get("ok"):
            raise ResolveError(f"GET {path} 业务错误：{body[:200]}")
        return payload.get("data")

    def resolve(self, item: str) -> str:
        """解析 llm-automation/<item>/api_key → 密钥值（内存态，不落盘不打印）。"""
        items = self._get("/api/v1/connect/items")
        entry = None
        for it in items:
            if item in (it.get("title"), it.get("name")):
                entry = it
                break
        if entry is None:
            raise ResolveError(f"scope 内找不到条目 {item!r}")
        detail = self._get(f"/api/v1/connect/items/{entry['id']}")
        data = detail.get("data") or {}
        variant = data.get("ApiKey") or data
        value = variant.get("api_key")
        if not isinstance(value, str) or not value:
            raise ResolveError(f"{item}: 详情里没有 api_key 字段（形态：{sorted(data)}）")
        value = normalize(value)
        try:
            ensure_safe_value(item, value)
        except UnsafeSecretValue as e:
            raise ResolveError(str(e)) from e
        return value


# ---------- 各类消费方校验 ----------

class CheckFailure:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def add(self, msg: str) -> None:
        self.errors.append(msg)


def check_material(
    name: str, spec: dict[str, Any], wanted: dict[str, str], fail: CheckFailure
) -> None:
    path = expand_path(spec["path"])
    if not path.exists():
        fail.add(f"material {name}: 文件不存在 {path}")
        return
    text = path.read_text(encoding="utf-8")
    block_vars, outside_vars, _, present = split_managed(text)
    if not present:
        fail.add(f"material {name}: 托管块缺失（跑 --apply）")
        return
    for var, value in wanted.items():
        if var not in block_vars:
            fail.add(f"material {name}: 托管块缺 {var}")
        elif normalize(block_vars[var]) != value:
            fail.add(f"material {name}: {var} 与 persona 值不一致（漂移）")
        if var in outside_vars:
            fail.add(f"material {name}: {var} 在块外另有定义（所有权漂移）")


def check_bifrost_key(name: str, db_path: str, value: str, fail: CheckFailure) -> None:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute("SELECT value FROM config_keys WHERE name=?", (name,)).fetchall()
        conn.close()
    except sqlite3.Error as e:
        fail.add(f"bifrost-key {name}: 打开库失败 {e}")
        return
    if not rows:
        fail.add(f"bifrost-key {name}: 不存在")
    elif normalize(rows[0][0] or "") != value:
        fail.add(f"bifrost-key {name}: 值与 persona 不一致（bifrost 侧已轮换？）")


def check_bifrost_vk(name: str, db_path: str, value: str, fail: CheckFailure) -> None:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT value FROM governance_virtual_keys WHERE name=?", (name,)
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        fail.add(f"bifrost-vk {name}: 打开库失败 {e}")
        return
    if not rows:
        fail.add(f"bifrost-vk {name}: 不存在")
    elif normalize(rows[0][0] or "") != value:
        fail.add(f"bifrost-vk {name}: 值与 persona 不一致")


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


def scan_plaintext_leaks(
    kind: str,
    file_path: Path,
    vault_values: dict[str, str],
    orphan_allow: dict[str, int],
    fail: CheckFailure,
) -> None:
    """配置文件明文扫描：任何 api_key 明文 == vault 值 → 失败；
    孤儿明文按 model 允许数核对（gaps.litellm_plaintext_orphans）。"""
    try:
        cfg = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        fail.add(f"{kind}-config: 解析失败 {e}")
        return
    vault_norm = {normalize(v) for v in vault_values.values()}
    plaintext_by_model: dict[str, int] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str) and path.endswith("api_key"):
            if node.startswith(("${", "os.environ/")):
                return
            model = "unknown"
            if kind == "litellm":
                m = re.match(r".*model_list\[(\d+)\]", path)
                if m:
                    try:
                        model = cfg["model_list"][int(m.group(1))].get("model_name", "unknown")
                    except (IndexError, KeyError, AttributeError):
                        model = "unknown"
            plaintext_by_model[model] = plaintext_by_model.get(model, 0) + 1
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


# ---------- 主流程 ----------

def load_fleet(path: Path) -> dict[str, Any]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or cfg.get("version") != 1:
        raise SystemExit("fleet.yaml: 需要 version: 1 的映射")
    items = cfg.get("providers") or []
    names = [p.get("item") for p in items]
    if any(not n for n in names):
        raise SystemExit("fleet.yaml: providers[].item 必填")
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise SystemExit(f"fleet.yaml: item 重复 {sorted(dup)}")
    for p in items:
        for c in p.get("consumers") or []:
            if c.get("target") not in {
                "litellm-env",
                "hermes-env",
                "hermes-config",
                "bifrost-key",
                "bifrost-vk",
                "magpie-caller",
            }:
                raise SystemExit(f"fleet.yaml: {p['item']} 未知 target {c.get('target')!r}")
    mats = cfg.get("materials") or {}
    if not isinstance(mats, dict) or not mats:
        raise SystemExit("fleet.yaml: materials 必须是非空映射")
    for name, spec in mats.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("path"), str):
            raise SystemExit(f"fleet.yaml: material {name}: 需要 path 字符串")
        validate_material_path(spec["path"], name)
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
        print(f"[fleet] FAIL resolve: {e}", file=sys.stderr)
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

    if args.apply:
        for name, spec in mats.items():
            path = expand_path(spec["path"])
            old = path.read_text(encoding="utf-8") if path.exists() else ""
            try:
                new, changes, conflicts = merge_managed_block(
                    old, wanted[name], args.force
                )
            except ValueError as e:  # UnsafeSecretValue / 空清单拒写
                print(f"[apply] {name}: FAIL {e}", file=sys.stderr)
                fail.add(f"{name}: {e}")
                continue
            if new != old:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(new, encoding="utf-8")
            if path.exists():
                os.chmod(path, int(spec.get("mode", "0600"), 8))
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
            t, tgt = c["target"], c.get("name")
            if t == "bifrost-key":
                check_bifrost_key(
                    tgt, str(expand_path(c.get("db", "~/.local/share/bifrost/data/config.db"))),
                    val, fail,
                )
            elif t == "bifrost-vk":
                check_bifrost_vk(
                    tgt, str(expand_path(c.get("db", "~/.local/share/bifrost/data/config.db"))),
                    val, fail,
                )
            elif t == "magpie-caller":
                check_magpie_caller(
                    tgt, expand_path(c.get("file", "~/.config/magpie/caller-keys.json")),
                    val, fail,
                )
            elif t == "hermes-config":
                hermes_cfg_path = expand_path(c.get("file", "~/.hermes/config.yaml"))
                check_hermes_refs(c["paths"], c["ref"], hermes_cfg_path, fail)

    # 明文泄漏扫描（两类配置）
    allow = {
        e["model"]: e["count"]
        for e in (cfg.get("gaps") or {}).get("litellm_plaintext_orphans", [])
    }
    scan_plaintext_leaks(
        "litellm", expand_path("~/.local/share/litellm/config.yaml"), values, allow, fail
    )
    if hermes_cfg_path is None:
        hermes_cfg_path = expand_path("~/.hermes/config.yaml")
    scan_plaintext_leaks("hermes", hermes_cfg_path, values, {}, fail)

    if fail.errors:
        print(f"[check] FAIL ({len(fail.errors)}):")
        for e in fail.errors:
            print(f"  - {e}")
        return 1
    print("[check] OK — 材料、引用、明文扫描全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
