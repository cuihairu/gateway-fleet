#!/usr/bin/env python3
"""fleet-apply 纯函数单测（不触网、不碰真实材料文件）。

运行：python3 -m unittest discover -s tests -v
"""
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
    def _scan(self, cfg: dict, vault: dict, allow: dict):
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False
        ) as f:
            import yaml as _yaml

            _yaml.safe_dump(cfg, f)
            path = Path(f.name)
        try:
            fail = fa.CheckFailure()
            fa.scan_plaintext_leaks("litellm", path, vault, allow, fail)
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
