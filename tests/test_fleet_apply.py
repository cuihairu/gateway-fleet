#!/usr/bin/env python3
"""fleet-apply 纯函数单测（不触网、不碰真实材料文件）。

运行：python3 -m unittest discover -s tests -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import fleet_apply as fa  # noqa: E402


class NormalizeTest(unittest.TestCase):
    def test_strips_double_quotes(self):
        self.assertEqual(fa.normalize('"sk-abc"'), "sk-abc")

    def test_strips_single_quotes(self):
        self.assertEqual(fa.normalize("'sk-abc'"), "sk-abc")

    def test_plain_untouched(self):
        self.assertEqual(fa.normalize("sk-abc"), "sk-abc")

    def test_mismatched_quotes_untouched(self):
        self.assertEqual(fa.normalize('"sk-abc\''), '"sk-abc\'')


class ExpandVarsTest(unittest.TestCase):
    def test_simple_var(self):
        self.assertEqual(fa.expand_vars("unix://${X}/p.sock", {"X": "/run/u"}), "unix:///run/u/p.sock")

    def test_default_used_when_missing(self):
        self.assertEqual(fa.expand_vars("${MISSING:-/fallback}", {}), "/fallback")

    def test_value_wins_over_default(self):
        self.assertEqual(fa.expand_vars("${V:-/fb}", {"V": "/real"}), "/real")

    def test_missing_without_default_kept(self):
        self.assertEqual(fa.expand_vars("${NOPE}", {}), "${NOPE}")


class EnvCodecTest(unittest.TestCase):
    def test_render_safe_raw(self):
        self.assertEqual(fa.render_value("sk-abc_1.2:x/y"), "sk-abc_1.2:x/y")

    def test_render_special_quoted(self):
        self.assertEqual(fa.render_value("a b"), '"a b"')

    def test_render_escapes_quote(self):
        self.assertEqual(fa.render_value('a"b'), '"a\\"b"')

    def test_parse_env_lines(self):
        text = "# c\nA=1\nB=\"two\"\nNOT A LINE\nC=3\n"
        self.assertEqual(fa.parse_env_lines(text), {"A": "1", "B": '"two"', "C": "3"})


class ManagedBlockTest(unittest.TestCase):
    OUTSIDE = "KEEP=1\n# comment\nOTHER=x\n"

    def test_insert_into_empty(self):
        new, changes, conflicts = fa.merge_managed_block("", {"A": "v1"}, force=False)
        self.assertIn(fa.BEGIN, new)
        self.assertIn("A=v1\n", new)
        self.assertIn(fa.END, new)
        self.assertEqual(conflicts, [])
        self.assertTrue(changes)

    def test_replace_block_preserves_outside(self):
        text = self.OUTSIDE + fa.render_block({"A": "old"}) + "TAIL=9\n"
        new, changes, _ = fa.merge_managed_block(text, {"A": "new"}, force=False)
        self.assertIn("KEEP=1\n", new)
        self.assertIn("TAIL=9\n", new)
        self.assertIn("A=new\n", new)
        self.assertNotIn("A=old", new)
        self.assertTrue(any("更新" in c for c in changes))

    def test_moves_same_value_outside_line_into_block(self):
        text = "A= same \nKEEP=1\n"  # 归一后同值
        new, changes, conflicts = fa.merge_managed_block(
            text, {"A": "same"}, force=False
        )
        self.assertEqual(conflicts, [])
        # 块外不再有 A，块内有 A
        block_vars, outside_vars, _, found = fa.split_managed(new)
        self.assertTrue(found)
        self.assertIn("A", block_vars)
        self.assertNotIn("A", outside_vars)
        self.assertIn("KEEP=1", new)

    def test_conflict_keeps_outside_value_unless_force(self):
        text = "A=different\nKEEP=1\n"
        new, changes, conflicts = fa.merge_managed_block(text, {"A": "persona"}, force=False)
        self.assertEqual(len(conflicts), 1)
        self.assertIn("A=different", new)          # 块外原值继续生效
        block_vars = fa.parse_env_lines(new.split(fa.BEGIN)[1].split(fa.END)[0])
        self.assertNotIn("A", block_vars)          # 冲突变量不进块
        self.assertIn("KEEP=1", new)

    def test_force_overwrites_conflict(self):
        text = "A=different\n"
        new, changes, conflicts = fa.merge_managed_block(text, {"A": "persona"}, force=True)
        self.assertEqual(conflicts, [])
        self.assertNotIn("A=different", new)
        block_vars = fa.parse_env_lines(new.split(fa.BEGIN)[1].split(fa.END)[0])
        self.assertEqual(fa.normalize(block_vars["A"]), "persona")

    def test_idempotent_second_run(self):
        first, _, _ = fa.merge_managed_block("", {"A": "v1", "B": "v2"}, force=False)
        second, changes, conflicts = fa.merge_managed_block(first, {"A": "v1", "B": "v2"}, force=False)
        self.assertEqual(first, second)
        self.assertEqual(changes, [])
        self.assertEqual(conflicts, [])

    def test_conflict_with_existing_block_preserves_block_value(self):
        text = fa.render_block({"A": "oldmanaged"}) + "A=different\nKEEP=1\n"
        new, changes, conflicts = fa.merge_managed_block(text, {"A": "persona"}, force=False)
        block_vars = fa.parse_env_lines(new.split(fa.BEGIN)[1].split(fa.END)[0])
        self.assertEqual(block_vars.get("A"), "oldmanaged")   # 块不清空
        self.assertIn("A=different", new)                     # 块外原值继续生效
        self.assertNotIn("A=persona", new)                    # persona 值不写入
        self.assertIn("KEEP=1", new)
        self.assertTrue(any("保留" in c for c in conflicts))

    def test_block_var_absent_from_wanted_preserved(self):
        text = fa.render_block({"OLD": "secret1", "A": "v1"})
        new, changes, _ = fa.merge_managed_block(text, {"A": "v1"}, force=False)
        block_vars = fa.parse_env_lines(new.split(fa.BEGIN)[1].split(fa.END)[0])
        self.assertEqual(block_vars.get("OLD"), "secret1")    # 清单收缩不删旧 secret
        self.assertEqual(block_vars.get("A"), "v1")
        self.assertTrue(any("OLD" in c for c in changes))

    def test_empty_wanted_refused(self):
        text = fa.render_block({"A": "v1"})
        with self.assertRaises(ValueError):
            fa.merge_managed_block(text, {}, force=False)
        # 原文本不受影响
        self.assertIn("A=v1", text)

    def test_preserved_var_text_stable_across_runs(self):
        first, _, _ = fa.merge_managed_block(
            fa.render_block({"OLD": "s1"}), {"A": "v"}, force=False
        )
        second, _, _ = fa.merge_managed_block(first, {"A": "v"}, force=False)
        self.assertEqual(first, second)


class YamlPathTest(unittest.TestCase):
    NODE = {"model": {"api_key": "x"}, "fallback_providers": [{"api_key": "y"}]}

    def test_nested(self):
        self.assertEqual(fa.yaml_get_path(self.NODE, "model.api_key"), "x")

    def test_index(self):
        self.assertEqual(fa.yaml_get_path(self.NODE, "fallback_providers[0].api_key"), "y")

    def test_missing_raises(self):
        with self.assertRaises(KeyError):
            fa.yaml_get_path(self.NODE, "model.nope")


class PlaintextScanTest(unittest.TestCase):
    def _scan(self, cfg: dict, vault: dict, allow: dict, kind: str = "litellm"):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False
        ) as f:
            import yaml as _yaml

            _yaml.safe_dump(cfg, f)
            path = Path(f.name)
        try:
            fail = fa.CheckFailure()
            fa.scan_plaintext_leaks(kind, path, vault, allow, fail)
            return fail.errors
        finally:
            path.unlink()

    def test_env_ref_ok(self):
        cfg = {"model_list": [
            {"model_name": "m1", "litellm_params": {"api_key": "os.environ/V"}}]}
        self.assertEqual(self._scan(cfg, {"m1": "sk-v"}, {}), [])

    def test_vault_value_in_plaintext_fails(self):
        cfg = {"model_list": [
            {"model_name": "m1", "litellm_params": {"api_key": "sk-v"}}]}
        errors = self._scan(cfg, {"m1": "sk-v"}, {"m1": 1})
        # 明文 == vault 值 → 泄漏失败（即使 allow 里登记了）
        self.assertTrue(any("明文存放 vault 值" in e for e in errors))

    def test_orphan_over_allowlist_fails(self):
        cfg = {"model_list": [
            {"model_name": "m1", "litellm_params": {"api_key": "orphan-key"}}]}
        errors = self._scan(cfg, {"other": "sk-x"}, {"m1": 0})
        self.assertTrue(any("明文行 1 > 允许" in e for e in errors))

    def test_orphan_allowlist_match_ok(self):
        cfg = {"model_list": [
            {"model_name": "m1", "litellm_params": {"api_key": "orphan-key"}}]}
        self.assertEqual(self._scan(cfg, {"other": "sk-x"}, {"m1": 1}), [])

    def test_orphan_below_registered_fails(self):
        cfg = {"model_list": []}
        errors = self._scan(cfg, {"other": "sk-x"}, {"m1": 1})
        self.assertTrue(any("补录后请收紧" in e for e in errors))

    def test_hermes_unregistered_plaintext_fails(self):
        cfg = {"providers": {"legacy": {"api_key": "orphan-hermes-key"}}}
        errors = self._scan(cfg, {"other": "sk-x"}, {}, kind="hermes")
        self.assertTrue(any("未登记明文" in e for e in errors))
        self.assertTrue(any("hermes.providers.legacy.api_key" in e for e in errors))

    def test_hermes_registered_plaintext_ok(self):
        cfg = {"providers": {"legacy": {"api_key": "orphan-hermes-key"}}}
        errors = self._scan(
            cfg, {"other": "sk-x"}, {"hermes.providers.legacy.api_key": 1}, kind="hermes"
        )
        self.assertEqual(errors, [])

    def test_hermes_env_ref_ok(self):
        cfg = {"providers": {"ark": {"api_key": "${ARK_API_KEY}"}}}
        self.assertEqual(self._scan(cfg, {"ark": "sk-v"}, {}, kind="hermes"), [])

    def test_hermes_vault_value_plaintext_fails(self):
        cfg = {"model": {"api_key": "sk-v"}}
        errors = self._scan(cfg, {"m": "sk-v"}, {}, kind="hermes")
        self.assertTrue(any("明文存放 vault 值" in e for e in errors))

    def test_hermes_below_registered_fails(self):
        cfg = {"providers": {}}
        errors = self._scan(
            cfg, {"other": "sk-x"}, {"hermes.providers.old.api_key": 1}, kind="hermes"
        )
        self.assertTrue(any("补录后请收紧" in e for e in errors))

    def test_variant_key_name_counts_as_slot(self):
        cfg = {"providers": {"legacy": {"api-key": "orphan-k"}}}
        errors = self._scan(cfg, {"other": "sk-x"}, {}, kind="hermes")
        self.assertTrue(any("hermes.providers.legacy.api-key" in e for e in errors))

    def test_variant_camel_case_vault_value_fails(self):
        cfg = {"auth": {"apiKey": "sk-v"}}
        errors = self._scan(cfg, {"m": "sk-v"}, {}, kind="hermes")
        self.assertTrue(any("明文存放 vault 值" in e for e in errors))

    def test_bare_list_value_under_key_family_counts(self):
        cfg = {"api_keys": ["orphan-k1"]}
        errors = self._scan(cfg, {"other": "sk-x"}, {}, kind="hermes")
        self.assertTrue(any("hermes.api_keys[0]" in e for e in errors))

    def test_bare_list_value_registered_ok(self):
        cfg = {"api_keys": ["orphan-k1"]}
        errors = self._scan(
            cfg, {"other": "sk-x"}, {"hermes.api_keys[0]": 1}, kind="hermes"
        )
        self.assertEqual(errors, [])

    def test_arbitrary_key_vault_equal_fails(self):
        cfg = {"weird_spot": "sk-v"}
        errors = self._scan(cfg, {"m": "sk-v"}, {}, kind="hermes")
        self.assertTrue(any("明文存放 vault 值" in e for e in errors))

    def test_benign_strings_not_flagged(self):
        cfg = {
            "model": "gpt-x",
            "note": "sk-v is not here",
            "url": "https://api.example.com",
            "api_base": "os.environ/V",
        }
        self.assertEqual(self._scan(cfg, {"m": "sk-v"}, {}, kind="hermes"), [])


class SecretInjectionGuardTest(unittest.TestCase):
    def test_render_rejects_newline(self):
        with self.assertRaises(fa.UnsafeSecretValue):
            fa.render_value("a\nb")

    def test_render_rejects_carriage_return_and_nul(self):
        for bad in ("a\rb", "a\x00b"):
            with self.assertRaises(fa.UnsafeSecretValue):
                fa.render_value(bad)

    def test_render_rejects_block_markers(self):
        with self.assertRaises(fa.UnsafeSecretValue):
            fa.render_value(fa.END)
        with self.assertRaises(fa.UnsafeSecretValue):
            fa.render_value(fa.BEGIN)

    def test_merge_rejects_block_escape_via_newline(self):
        evil = 'x\n# <<< gateway-fleet <<<\nEVIL=1\n# >>> gateway-fleet managed (fleet-apply) >>>'
        with self.assertRaises(fa.UnsafeSecretValue):
            fa.merge_managed_block("", {"A": evil}, force=False)

    def test_safe_special_chars_still_quoted(self):
        self.assertEqual(fa.render_value("a b"), '"a b"')
        new, _, _ = fa.merge_managed_block("", {"A": "sk-abc_1.2:x/y+z"}, force=False)
        self.assertIn("A=sk-abc_1.2:x/y+z\n", new)


class BifrostCheckTest(unittest.TestCase):
    def _make_db(self, d: Path) -> Path:
        db = d / "config.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE config_keys (name TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO config_keys VALUES (?, ?)", ("zai-glm", "sk-live-value")
        )
        conn.commit()
        conn.execute("PRAGMA journal_mode=wal")  # WAL 库是副作用最典型的形态
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        return db

    def test_matching_key_ok_and_no_side_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            db = self._make_db(d)
            baseline = {p.name for p in d.iterdir()}
            f = fa.CheckFailure()
            fa.check_bifrost_key("zai-glm", str(db), "sk-live-value", f)
            self.assertEqual(f.errors, [])
            after = {p.name for p in d.iterdir()}
            self.assertEqual(after, baseline)  # 无 -shm/-journal 新增
            self.assertFalse(any("shm" in n for n in after))
            self.assertFalse(any(n.endswith("-journal") for n in after))

    def test_drift_reported(self):
        with tempfile.TemporaryDirectory() as d:
            db = self._make_db(Path(d))
            f = fa.CheckFailure()
            fa.check_bifrost_key("zai-glm", str(db), "sk-rotated", f)
            self.assertTrue(any("不一致" in e for e in f.errors))

    def test_missing_key_reported(self):
        with tempfile.TemporaryDirectory() as d:
            db = self._make_db(Path(d))
            f = fa.CheckFailure()
            fa.check_bifrost_key("nope", str(db), "x", f)
            self.assertTrue(any("不存在" in e for e in f.errors))

    def test_missing_file_reported(self):
        with tempfile.TemporaryDirectory() as d:
            f = fa.CheckFailure()
            fa.check_bifrost_key("x", str(Path(d) / "absent.db"), "v", f)
            self.assertTrue(any("库文件不存在" in e for e in f.errors))

    def test_missing_table_schema_reported(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "config.db"
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE something_else (x TEXT)")
            conn.commit()
            conn.close()
            f = fa.CheckFailure()
            fa.check_bifrost_key("x", str(db), "v", f)
            self.assertTrue(any("schema 不符" in e for e in f.errors))

    def test_vk_missing_table_reported(self):
        with tempfile.TemporaryDirectory() as d:
            db = self._make_db(Path(d))
            f = fa.CheckFailure()
            fa.check_bifrost_vk("vk-main", str(db), "v", f)
            self.assertTrue(any("schema 不符" in e for e in f.errors))


class FakeResolver(fa.ConnectResolver):
    """注入 _get 的假 resolver——测协议形态鲁棒性，不触网。"""

    def __init__(self, responses):
        super().__init__("/tmp/unused.sock", "t")
        self.responses = responses

    def _get(self, path):
        return self.responses[path]


ITEMS = [{"title": "zai-glm", "id": 7}]
DETAIL_OK = {"data": {"ApiKey": {"api_key": "sk-real"}}}


class SecretRefProtocolTest(unittest.TestCase):
    def test_happy_path(self):
        r = FakeResolver({
            "/api/v1/connect/items": ITEMS,
            "/api/v1/connect/items/7": DETAIL_OK,
        })
        self.assertEqual(r.resolve("zai-glm"), "sk-real")

    def test_wraps_protocol_type_error(self):
        r = FakeResolver({
            "/api/v1/connect/items": "not-a-list",
            "/api/v1/connect/items/7": DETAIL_OK,
        })
        with self.assertRaises(fa.ResolveError):
            r.resolve("zai-glm")

    def test_wraps_missing_id(self):
        r = FakeResolver({
            "/api/v1/connect/items": [{"title": "zai-glm"}],
            "/api/v1/connect/items/None": DETAIL_OK,
        })
        with self.assertRaises(fa.ResolveError):
            r.resolve("zai-glm")

    def test_wraps_non_dict_detail(self):
        r = FakeResolver({
            "/api/v1/connect/items": ITEMS,
            "/api/v1/connect/items/7": [1, 2, 3],
        })
        with self.assertRaises(fa.ResolveError):
            r.resolve("zai-glm")

    def test_wraps_missing_data_object(self):
        r = FakeResolver({
            "/api/v1/connect/items": ITEMS,
            "/api/v1/connect/items/7": {"data": None},
        })
        with self.assertRaises(fa.ResolveError):
            r.resolve("zai-glm")

    def test_precise_field_missing_message(self):
        r = FakeResolver({
            "/api/v1/connect/items": ITEMS,
            "/api/v1/connect/items/7": {"data": {"ApiKey": {"other": 1}}},
        })
        with self.assertRaises(fa.ResolveError) as ctx:
            r.resolve("zai-glm")
        self.assertIn("api_key", str(ctx.exception))

    def test_item_not_in_list(self):
        r = FakeResolver({
            "/api/v1/connect/items": [],
            "/api/v1/connect/items/7": DETAIL_OK,
        })
        with self.assertRaises(fa.ResolveError):
            r.resolve("zai-glm")


class BlockStructureTest(unittest.TestCase):
    def test_clean_block_has_no_issues(self):
        self.assertEqual(
            fa.block_structure_issues(fa.render_block({"A": "1"})), []
        )

    def test_duplicate_begin_reported(self):
        text = f"{fa.BEGIN}\nA=1\n{fa.BEGIN}\nA=1\n{fa.END}\n"
        self.assertTrue(any("重复" in i for i in fa.block_structure_issues(text)))

    def test_unclosed_begin_reported(self):
        self.assertTrue(
            any("未闭合" in i for i in fa.block_structure_issues(f"{fa.BEGIN}\nA=1\n"))
        )

    def test_end_outside_block_reported(self):
        self.assertTrue(
            any("错序" in i for i in fa.block_structure_issues(f"{fa.END}\nA=1\n"))
        )

    def test_apply_refuses_broken_structure(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            path.write_text(f"{fa.BEGIN}\nA=old\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                fa.apply_material(
                    "m", {"path": str(path), "mode": "0600"}, {"A": "new"}, force=False
                )
            self.assertIn("A=old", path.read_text(encoding="utf-8"))

    def test_check_reports_structural_anomaly(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            path.write_text(f"{fa.END}\nA=1\n", encoding="utf-8")
            f = fa.CheckFailure()
            fa.check_material("m", {"path": str(path)}, {"A": "x"}, f)
            self.assertTrue(any("结构异常" in e for e in f.errors))

    def test_check_reports_reset_block(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            path.write_text(fa.render_block({}) + "A=1\n", encoding="utf-8")
            f = fa.CheckFailure()
            fa.check_material("m", {"path": str(path)}, {"A": "x"}, f)
            self.assertTrue(any("疑似被重置" in e for e in f.errors))
            self.assertTrue(any("托管块缺 A" in e for e in f.errors))


class ApplyMaterialTest(unittest.TestCase):
    def _spec(self, path: Path, mode: str = "0600") -> dict:
        return {"path": str(path), "mode": mode}

    def test_creates_new_file_0600_with_block(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            changes, conflicts = fa.apply_material(
                "m", self._spec(path), {"A": "v1"}, force=False
            )
            self.assertEqual(conflicts, [])
            self.assertTrue(changes)
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            block_vars, _, _, found = fa.split_managed(path.read_text(encoding="utf-8"))
            self.assertTrue(found)
            self.assertEqual(block_vars["A"], "v1")

    def test_outside_content_preserved_and_mode_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            path.write_text("KEEP=1\n", encoding="utf-8")
            os.chmod(path, 0o600)
            fa.apply_material("m", self._spec(path), {"A": "v1"}, force=False)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("KEEP=1\n"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_conflict_keeps_outside_value_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            path.write_text("A=different\nKEEP=1\n", encoding="utf-8")
            changes, conflicts = fa.apply_material(
                "m", self._spec(path), {"A": "persona"}, force=False
            )
            self.assertEqual(len(conflicts), 1)
            text = path.read_text(encoding="utf-8")
            self.assertIn("A=different", text)
            self.assertIn("KEEP=1", text)

    def test_snapshot_written_when_content_changes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            path.write_text(fa.render_block({"A": "old"}) + "KEEP=1\n", encoding="utf-8")
            os.chmod(path, 0o600)
            fa.apply_material("m", self._spec(path), {"A": "new"}, force=False)
            snap = path.with_name(path.name + ".fleet-bak")
            self.assertTrue(snap.exists())
            self.assertIn("A=old", snap.read_text(encoding="utf-8"))
            self.assertEqual(snap.stat().st_mode & 0o777, 0o600)
            self.assertIn("A=new", path.read_text(encoding="utf-8"))

    def test_no_change_writes_no_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            fa.apply_material("m", self._spec(path), {"A": "v1"}, force=False)
            fa.apply_material("m", self._spec(path), {"A": "v1"}, force=False)
            snap = path.with_name(path.name + ".fleet-bak")
            self.assertFalse(snap.exists())

    def test_no_temp_file_left_behind(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            fa.apply_material("m", self._spec(path), {"A": "v1"}, force=False)
            leftovers = [p for p in Path(d).iterdir() if ".tmp-" in p.name]
            self.assertEqual(leftovers, [])

    def test_lock_timeout_when_held_by_other(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            lock_path = path.with_name(path.name + ".fleet-lock")
            with open(lock_path, "w") as holder:
                import fcntl

                fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
                old_retry = fa.LOCK_RETRY
                fa.LOCK_RETRY = 0
                try:
                    with self.assertRaises(RuntimeError):
                        fa.apply_material("m", self._spec(path), {"A": "v1"}, force=False)
                finally:
                    fa.LOCK_RETRY = old_retry
                    fcntl.flock(holder, fcntl.LOCK_UN)


class CheckMaterialIntegrationTest(unittest.TestCase):
    """真实文件上的 apply→check 闭环：读写、冲突、权限、异常路径。"""

    def _material(self, d, text=None, mode=0o600):
        path = Path(d) / "env.systemd"
        if text is not None:
            path.write_text(text, encoding="utf-8")
        os.chmod(path, mode)
        return path

    def test_missing_file_reported(self):
        with tempfile.TemporaryDirectory() as d:
            f = fa.CheckFailure()
            fa.check_material("m", {"path": str(Path(d) / "absent")}, {"A": "v"}, f)
            self.assertTrue(any("文件不存在" in e for e in f.errors))

    def test_apply_then_check_green_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            fa.apply_material(
                "m", {"path": str(path), "mode": "0600"}, {"A": "v1"}, force=False
            )
            f = fa.CheckFailure()
            fa.check_material("m", {"path": str(path)}, {"A": "v1"}, f)
            self.assertEqual(f.errors, [])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_drift_detected_after_external_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            fa.apply_material(
                "m", {"path": str(path), "mode": "0600"}, {"A": "v1"}, force=False
            )
            tampered = path.read_text(encoding="utf-8").replace("A=v1", "A=v2")
            path.write_text(tampered, encoding="utf-8")
            f = fa.CheckFailure()
            fa.check_material("m", {"path": str(path)}, {"A": "v1"}, f)
            self.assertTrue(any("不一致（漂移）" in e for e in f.errors))

    def test_outside_ownership_drift_detected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            fa.apply_material(
                "m", {"path": str(path), "mode": "0600"}, {"A": "v1"}, force=False
            )
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("A=v1\n")  # 块外重复定义
            f = fa.CheckFailure()
            fa.check_material("m", {"path": str(path)}, {"A": "v1"}, f)
            self.assertTrue(any("所有权漂移" in e for e in f.errors))

    def test_apply_normalizes_loose_mode(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._material(d, "KEEP=1\n", mode=0o644)
            fa.apply_material(
                "m", {"path": str(path), "mode": "0600"}, {"A": "v1"}, force=False
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rollback_from_snapshot_restores_original(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "env.systemd"
            original = "KEEP=1\n" + fa.render_block({"A": "old"})
            self._material(d, original)
            fa.apply_material(
                "m", {"path": str(path), "mode": "0600"}, {"A": "new"}, force=False
            )
            snap = path.with_name(path.name + ".fleet-bak")
            path.write_text(snap.read_text(encoding="utf-8"), encoding="utf-8")
            os.chmod(path, 0o600)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    @unittest.skipIf(os.geteuid() == 0, "root ignores file modes")
    def test_unreadable_file_reported_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._material(d, fa.render_block({"A": "v"}))
            os.chmod(path, 0o000)
            try:
                f = fa.CheckFailure()
                fa.check_material("m", {"path": str(path)}, {"A": "v"}, f)
                self.assertTrue(any("读取失败" in e for e in f.errors))
            finally:
                os.chmod(path, 0o600)

    def test_symlinked_material_rejected_at_check(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "real.env"
            real.write_text(fa.render_block({"A": "v"}), encoding="utf-8")
            link = Path(d) / "env.systemd"
            link.symlink_to(real)
            f = fa.CheckFailure()
            fa.check_material("m", {"path": str(link)}, {"A": "v"}, f)
            self.assertTrue(any("符号链接" in e for e in f.errors))


class SanitizeTest(unittest.TestCase):
    def setUp(self):
        fa._redactions.clear()
        self.addCleanup(fa._redactions.clear)

    def test_redacts_registered_value(self):
        fa.register_redaction("sk-secret123")
        self.assertEqual(fa.sanitize("got sk-secret123 in body"), "got *** in body")

    def test_longest_match_wins(self):
        fa.register_redaction("sk-short")
        fa.register_redaction("sk-short-plus")
        out = fa.sanitize("x sk-short-plus y sk-short z")
        self.assertNotIn("sk-short", out)
        self.assertEqual(out.count("***"), 2)

    def test_unregistered_values_untouched(self):
        self.assertEqual(fa.sanitize("plain message"), "plain message")

    def test_checkfailure_add_sanitizes(self):
        fa.register_redaction("sk-v")
        f = fa.CheckFailure()
        f.add("hermes-config: 解析失败 snippet api_key: sk-v end")
        self.assertNotIn("sk-v", f.errors[0])
        self.assertIn("***", f.errors[0])

    def test_empty_value_not_registered(self):
        fa.register_redaction("")
        self.assertEqual(fa._redactions, [])


class MaterialPathValidationTest(unittest.TestCase):
    def test_rejects_relative_path(self):
        with self.assertRaises(SystemExit):
            fa.validate_material_path("relative/env.systemd", "m")

    def test_rejects_dotdot_traversal(self):
        with self.assertRaises(SystemExit):
            fa.validate_material_path("/opt/app/../../etc/env.systemd", "m")

    def test_rejects_symlink_target(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "real.env"
            real.write_text("")
            link = Path(d) / "link.env"
            link.symlink_to(real)
            with self.assertRaises(SystemExit):
                fa.validate_material_path(str(link), "m")

    def test_rejects_symlink_ancestor(self):
        with tempfile.TemporaryDirectory() as d:
            realdir = Path(d) / "realdir"
            realdir.mkdir()
            linkdir = Path(d) / "linkdir"
            linkdir.symlink_to(realdir, target_is_directory=True)
            with self.assertRaises(SystemExit):
                fa.validate_material_path(str(linkdir / "env.systemd"), "m")

    def test_accepts_plain_absolute_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = fa.validate_material_path(str(Path(d) / "env.systemd"), "m")
            self.assertTrue(p.is_absolute())

    def test_repo_materials_paths_all_valid(self):
        cfg = fa.load_fleet(Path(__file__).resolve().parent.parent / "fleet.yaml")
        for name, spec in cfg["materials"].items():
            self.assertTrue(fa.validate_material_path(spec["path"], name).is_absolute())


class FleetYamlSchemaStrictTest(unittest.TestCase):
    MINIMAL = """version: 1
vault:
  identity: llm-automation
  endpoint: "unix:///tmp/x.sock"
  token_env: T
materials:
  m1:
    path: /tmp/fleet-test-material.env
    mode: "0600"
providers:
  - item: it1
    consumers:
      - target: litellm-env
        vars: [V1]
"""

    def _loads(self, text: str):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(text)
            p = Path(f.name)
        try:
            return fa.load_fleet(p)
        finally:
            p.unlink()

    def _rejects(self, text: str, needle: str):
        with self.assertRaises(SystemExit) as ctx:
            self._loads(text)
        self.assertIn(needle, str(ctx.exception))

    def test_minimal_ok(self):
        cfg = self._loads(self.MINIMAL)
        self.assertEqual(cfg["providers"][0]["item"], "it1")

    def test_bad_version_rejected(self):
        self._rejects(self.MINIMAL.replace("version: 1", "version: 2"), "version")

    def test_missing_vault_rejected(self):
        self._rejects(
            self.MINIMAL.replace("  identity: llm-automation\n", ""), "vault.identity"
        )

    def test_unknown_target_rejected(self):
        self._rejects(
            self.MINIMAL.replace("target: litellm-env", "target: nope"), "未知 target"
        )

    def test_env_consumer_without_vars_rejected(self):
        self._rejects(
            self.MINIMAL.replace("        vars: [V1]\n", ""), "vars"
        )

    def test_hermes_config_requires_ref_and_paths(self):
        text = self.MINIMAL.replace(
            """      - target: litellm-env
        vars: [V1]""",
            '''      - target: hermes-config
        ref: "${V1}"''',
        )
        self._rejects(text, "paths")

    def test_bifrost_key_requires_name(self):
        text = self.MINIMAL.replace(
            """      - target: litellm-env
        vars: [V1]""",
            "      - target: bifrost-key",
        )
        self._rejects(text, "name")

    def test_bad_mode_rejected(self):
        self._rejects(self.MINIMAL.replace('mode: "0600"', "mode: abc"), "mode")

    def test_gap_orphan_requires_model_and_count(self):
        self._rejects(
            self.MINIMAL
            + "gaps:\n  litellm_plaintext_orphans:\n    - model: m1\n",
            "count",
        )

    def test_vault_no_consumer_unknown_item_rejected(self):
        self._rejects(
            self.MINIMAL + "gaps:\n  vault_no_consumer: [ghost-item]\n", "ghost-item"
        )

    def test_duplicate_item_rejected(self):
        dup = self.MINIMAL.replace(
            "providers:\n",
            "providers:\n  - item: it1\n    consumers: []\n",
        )
        self._rejects(dup, "重复")

    def test_relative_material_path_rejected(self):
        self._rejects(
            self.MINIMAL.replace(
                "path: /tmp/fleet-test-material.env", "path: relative/x.env"
            ),
            "绝对路径",
        )

    def test_repo_fleet_yaml_passes_strict_schema(self):
        cfg = fa.load_fleet(Path(__file__).resolve().parent.parent / "fleet.yaml")
        self.assertGreaterEqual(len(cfg["providers"]), 20)

    def test_unknown_check_target_key_rejected(self):
        self._rejects(
            self.MINIMAL + "check_targets:\n  bogus: /tmp/x\n", "未知键"
        )

    def test_nonstring_check_target_rejected(self):
        self._rejects(
            self.MINIMAL + "check_targets:\n  litellm_config: 123\n", "check_targets"
        )

    def test_repo_fleet_yaml_declares_all_targets(self):
        cfg = fa.load_fleet(Path(__file__).resolve().parent.parent / "fleet.yaml")
        targets = cfg["check_targets"]
        self.assertEqual(
            set(targets),
            {"litellm_config", "hermes_config", "bifrost_db", "magpie_caller_keys"},
        )
        for v in targets.values():
            self.assertIsInstance(v, str)


class FleetYamlSchemaTest(unittest.TestCase):
    def test_repo_fleet_yaml_loads(self):
        repo = Path(__file__).resolve().parent.parent
        raw = yaml.safe_load((repo / "fleet.yaml").read_text(encoding="utf-8"))
        cfg = fa.load_fleet(repo / "fleet.yaml")
        # 数量从 fleet.yaml 推导（扩 vault 不再破测试）；硬性约束单独断言
        self.assertEqual(len(cfg["providers"]), len(raw["providers"]))
        self.assertEqual(len(cfg["providers"]), 24)  # 2026-11-14 现状，人工核对
        items = [p["item"] for p in cfg["providers"]]
        self.assertEqual(len(set(items)), len(items))  # item 唯一
        self.assertIn("litellm-env", cfg["materials"])
        self.assertIn("hermes-env", cfg["materials"])
        # gaps 登记的无消费方条目必须真实存在
        for name in (cfg.get("gaps") or {}).get("vault_no_consumer", []):
            self.assertIn(name, items)


if __name__ == "__main__":
    unittest.main()
