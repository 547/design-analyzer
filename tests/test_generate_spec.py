"""design-analyzer 回归测试。

分两层：
1. 纯逻辑单测（颜色合成 / get_color 统一路由 / 边框）——合成数据，确定性强，零外部依赖，始终运行。
2. 真实 JSON 集成测试——自动发现 skill 根目录下 design-analyzer/*.json（被 .gitignore 忽略，
   本地有 fixture 时运行，CI/无 fixture 环境自动跳过）。满足「用真实设计稿 JSON 验证」的要求，
   同时不会在无 fixture 的干净环境误报错。

运行：
    python3 -m unittest tests.test_generate_spec -v
    # 或
    python3 tests/test_generate_spec.py
"""

import glob
import os
import re
import sys
import unittest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))
import generate_spec as gs  # noqa: E402

HEX6 = re.compile(r"^#[0-9A-Fa-f]{6}$")
# 文档里出现的所有 #XXXXXX（背景/文字/边框色）
ANY_HEX = re.compile(r"#[0-9A-Fa-f]{6}")
# 页面级背景行： **背景**: #F2F2F2
PAGE_BG = re.compile(r"\*\*背景\*\*:\s*(#\w+)")
# 节点行内联的 bg= / 文字颜色 #xxx
BG_INLINE = re.compile(r"bg=(#[0-9A-Fa-f]{6})")


def _color(r, g, b, a=1):
    return {"r": r, "g": g, "b": b, "a": a}


def _raw_fill_node(*colors):
    """构造原始 JSON 节点（fill.colors[].value）。"""
    return {"fill": {"colors": [{"value": c} for c in colors]}}


def _tree_fill_node(*colors):
    """构造已构建的树节点（fills 颜色字符串列表）。"""
    return {"fills": list(colors)}


class TestColorCompositing(unittest.TestCase):
    """多层叠加 alpha 合成：最终可见 hex，无原始分层。"""

    def test_single_layer_passthrough(self):
        self.assertEqual(gs._composite_layers(["#F03C3C"]), "#F03C3C")

    def test_empty(self):
        self.assertEqual(gs._composite_layers([]), "")

    def test_red_over_white09(self):
        # #F03C3C + 白 0.9 → 期望 #FEECEC（用户最初踩坑的用例）
        out = gs._composite_layers(["#F03C3C", "rgba(255,255,255,0.9)"])
        self.assertTrue(HEX6.match(out), out)
        self.assertEqual(out, "#FEECEC")

    def test_opaque_intermediate_overrides(self):
        # 中间层不透明则覆盖底层，再与上层半透明混合
        out = gs._composite_layers(["#F03C3C", "#FFFFFF", "rgba(0,0,0,0.5)"])
        self.assertEqual(out, "#808080")

    def test_eight_digit_hex_alpha(self):
        # #FFFFFFE6 → 白 a≈0.902，与红底合成
        out = gs._composite_layers(["#F03C3C", "#FFFFFFE6"])
        self.assertTrue(HEX6.match(out), out)

    def test_arbitrary_colors(self):
        out = gs._composite_layers(["#123456", "rgba(200,100,50,0.3)"])
        self.assertTrue(HEX6.match(out), out)


class TestGetColorUnified(unittest.TestCase):
    """所有取色都应走 get_color(node, kind)，兼容两种节点形态。"""

    def test_fill_raw_node(self):
        node = _raw_fill_node(_color(240, 60, 60, 1), _color(255, 255, 255, 0.9))
        self.assertEqual(gs.get_color(node, "fill"), "#FEECEC")

    def test_fill_tree_node(self):
        node = _tree_fill_node("#F03C3C", "rgba(255,255,255,0.9)")
        self.assertEqual(gs.get_color(node, "fill"), "#FEECEC")

    def test_fill_no_color(self):
        self.assertEqual(gs.get_color({"basic": {}}, "fill"), "")

    def test_border_tree_node(self):
        node = {"border_info": ["1px #D8D8D8"]}
        self.assertEqual(gs.get_color(node, "border"), ["1px #D8D8D8"])

    def test_border_raw_node(self):
        node = {"stroke": {"borders": [
            {"strokeWidth": 1, "color": {"value": _color(216, 216, 216, 1)}}
        ]}}
        self.assertEqual(gs.get_color(node, "border"), ["1px #D8D8D8"])

    def test_border_no_border(self):
        self.assertEqual(gs.get_color({"basic": {}}, "border"), [])


class TestGeneratePageBg(unittest.TestCase):
    """端到端：页面级背景必须走 get_color 合成，不能输出底层原始色。"""

    def _root(self, *fill_colors):
        return {
            "type": "FRAME",
            "basic": {"name": "Root", "realType": "FRAME", "id": "root"},
            "bounds": {"top": 0, "left": 0, "width": 375, "height": 812},
            "fill": {"colors": [{"value": c} for c in fill_colors]},
            "children": [],
        }

    def test_page_bg_composites_two_layers(self):
        md = gs.generate(self._root(_color(240, 60, 60, 1), _color(255, 255, 255, 0.9)))
        m = PAGE_BG.search(md)
        self.assertIsNotNone(m, "页面背景行缺失")
        self.assertEqual(m.group(1), "#FEECEC")
        # 底层原始色不得作为页面背景出现
        self.assertNotIn("**背景**: #F03C3C", md)

    def test_page_bg_single_layer(self):
        md = gs.generate(self._root(_color(242, 242, 242, 1)))
        m = PAGE_BG.search(md)
        self.assertEqual(m.group(1), "#F2F2F2")


class TestRealJsonIntegration(unittest.TestCase):
    """真实设计稿 JSON 端到端：跑通整条流水线并断言不变量。

    fixture 位于被 .gitignore 的 design-analyzer/ 目录；本地有则运行，无则跳过。
    """

    json_files = sorted(
        glob.glob(os.path.join(SKILL_DIR, "design-analyzer", "*.json"))
    )

    @unittest.skipIf(not json_files, "未找到 design-analyzer/*.json fixture（被 gitignore，本地才有）")
    def test_all_fixtures_generate_without_error(self):
        for path in self.json_files:
            with self.subTest(json=os.path.basename(path)):
                with open(path, encoding="utf-8") as f:
                    flat = __import__("json").load(f)
                root = gs.build_tree_from_flat_list(flat)
                md = gs.generate(root, 375)

                # 基本结构
                self.assertTrue(md.startswith("# 设计规格文档"), "缺少标题")
                # 页面背景是合法 hex
                m = PAGE_BG.search(md)
                self.assertIsNotNone(m, f"{path} 缺页面背景行")
                self.assertTrue(HEX6.match(m.group(1)), f"{path} 背景非 hex: {m.group(1)}")
                # 绝不残留被删除的「合成注释」
                self.assertNotIn("/*", md, f"{path} 仍含 /* 合成注释")
                # 所有内联 bg= 都是合法 hex（无 rgba、无注释）
                for bg in BG_INLINE.findall(md):
                    self.assertTrue(HEX6.match(bg), f"{path} 内联背景非 hex: {bg}")
                # 文档里出现的每个 #XXXXXX 都应是 6 位 hex（不该有 8 位/private 色）
                for h in ANY_HEX.findall(md):
                    self.assertTrue(HEX6.match(h), f"{path} 非法色值: {h}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
