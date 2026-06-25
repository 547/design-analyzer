#!/usr/bin/env python3
"""生成设计规格文档（Markdown）。

从摹客图层 JSON 提取：色值、坐标、字号、圆角、间距 ✅
结合截图确认：文字内容 ⚠️（JSON 可能不准）

用法:
  python3 generate_spec.py layerData.json      # 输出到当前目录 spec_页面名.md
  python3 generate_spec.py layerData.json -o output.md
"""

import json, argparse, sys
from collections import Counter


def to_hex(rgb):
    return f"#{round(rgb['r']):02X}{round(rgb['g']):02X}{round(rgb['b']):02X}"


def px2rpx(px, design_width=375):
    return round(px * 750 / design_width)


# ---- 数据提取 ----

def get_type(node):
    return node.get("basic", {}).get("realType", "")


def get_name(node):
    return node.get("basic", {}).get("name", "")


def get_bounds(node):
    b = node.get("bounds", {})
    return {
        "top": b.get("top", 0), "left": b.get("left", 0),
        "width": b.get("width", 0), "height": b.get("height", 0),
    }


def get_fill_colors(node):
    """提取 fill 中的颜色（适用于 FRAME/RECTANGLE）"""
    result = []
    for c in node.get("fill", {}).get("colors", []):
        v = c.get("value", {})
        result.append(to_hex(v))
    return result


def get_radius(node):
    r = node.get("stroke", {}).get("radius", [])
    return r if r and r not in ([0], [0, 0, 0, 0]) else []


def get_border_count(node):
    return len(node.get("stroke", {}).get("borders", []))


def get_text_info(node):
    """提取文字内容和样式（摹客/Figma 的 text.styles 结构）"""
    text_data = node.get("text", {})
    styles = text_data.get("styles", [])
    if not styles:
        # 回退：text.characters
        chars = text_data.get("characters", "")
        if chars:
            return [{
                "text": chars,
                "fontSize": None,
                "fontWeight": None,
                "color": "",
            }]
        return []

    result = []
    for s in styles:
        font = s.get("font", {})
        color = font.get("color", {})
        color_hex = to_hex(color.get("value", {})) if color.get("value") else ""
        result.append({
            "text": s.get("value", ""),
            "fontSize": font.get("size") or font.get("fontSize"),
            "fontWeight": font.get("fontWeight") or font.get("weight"),
            "color": color_hex,
            "lineHeight": s.get("space", {}).get("lineHeight"),
        })
    return result


# ---- 树结构 ----

def has_content(node):
    """节点或其子孙是否有实质内容"""
    texts = get_text_info(node)
    if texts:
        return True
    fills = get_fill_colors(node)
    if fills:
        return True
    if get_radius(node) or get_border_count(node):
        return True
    for child in node.get("children", []):
        if has_content(child):
            return True
    return False


def build_tree(node, depth=0):
    """递归构建可读的区块树"""
    ntype = get_type(node)
    nname = get_name(node)
    bn = get_bounds(node)
    if ntype == "TEXT":
        ti = get_text_info(node)
        if ti:
            return {"type": ntype, "name": nname, "bounds": bn, "texts": ti, "children": []}
        return None

    if ntype not in ("FRAME", "GROUP"):
        # VECTOR/RECTANGLE 有颜色或圆角才保留
        fills = get_fill_colors(node)
        radius = get_radius(node)
        borders = get_border_count(node)
        if fills or radius or borders:
            return {
                "type": ntype, "name": nname, "bounds": bn,
                "fills": fills, "radius": radius, "borders": borders,
                "texts": [], "children": [],
            }
        return None

    # FRAME/GROUP
    fills = get_fill_colors(node)
    radius = get_radius(node)
    borders = get_border_count(node)

    # 收集有内容的子节点
    children = []
    for child in node.get("children", []):
        sub = build_tree(child, depth + 1)
        if sub:
            children.append(sub)

    # 如果所有子节点都是 TEXT，提升到 texts
    if children and all(c["type"] == "TEXT" for c in children):
        texts = []
        for c in children:
            texts.extend(c.get("texts", []))
        # 保留 TEXT 节点的颜色信息
        tcolors = []
        for c in children:
            for t in c.get("texts", []):
                if t.get("color"):
                    tcolors.append(t["color"])
        return {
            "type": ntype, "name": nname, "bounds": bn,
            "fills": fills or tcolors, "radius": radius, "borders": borders,
            "texts": texts, "children": [],
        }

    return {
        "type": ntype, "name": nname, "bounds": bn,
        "fills": fills, "radius": radius, "borders": borders,
        "texts": [], "children": children,
    }


def merge_repeats(children):
    """合并连续重复的子树"""
    if len(children) < 2:
        return children

    result = []
    i = 0
    while i < len(children):
        cur = children[i]
        # 计算当前子树的"签名"（用于比较是否重复）
        cur_sig = _signature(cur)

        if cur_sig and i + 1 < len(children):
            count = 1
            j = i + 1
            while j < len(children):
                if _signature(children[j]) == cur_sig:
                    count += 1
                    j += 1
                else:
                    break
            if count > 1:
                cur["repeat"] = count
                result.append(cur)
                i = j
                continue

        result.append(cur)
        i += 1

    return result


def _signature(node):
    """生成子树签名：type + texts 内容 + fills + 子节点数"""
    parts = [node.get("type", "")]
    for t in node.get("texts", []):
        parts.append(t.get("text", ""))
    for f in node.get("fills", []):
        parts.append(f)
    r = node.get("radius", [])
    if r:
        parts.append(f"r{r}")
    parts.append(f"c{len(node.get('children', []))}")
    return tuple(parts) if len(parts) > 1 else None


# ---- 文档生成 ----

def generate(root, design_width=375):
    tree = build_tree(root)
    if not tree:
        tree = {"type": "FRAME", "name": "Root", "bounds": get_bounds(root),
                "fills": get_fill_colors(root), "texts": [], "children": []}

    lines = []
    lines.append("# 设计规格文档\n")
    bn = get_bounds(root)
    fills = get_fill_colors(root)
    bg = fills[0] if fills else "?"
    lines.append(f"**页面**: {bn['width']:.0f}×{bn['height']:.0f}px | **背景**: {bg} | rpx: 1px≈{px2rpx(1)}rpx\n")
    lines.append("> ✅ 色值/坐标/字号/圆角/间距 = JSON 精确值")
    lines.append("> ⚠️ 文字 = JSON 值，请对照截图填写右侧确认列\n")

    _render(tree, lines, 0)

    # 颜色表
    cc = Counter()
    _collect_colors(root, cc)
    lines.append("\n---\n## 颜色表")
    lines.append("| 色值 | 次数 | 推测用途 |")
    lines.append("|---|---|---|")
    usage = {
        "#F2F2F2": "页面背景", "#FFFFFF": "卡片/行底色",
        "#3975C6": "导航栏", "#2C2C2C": "页面标题",
        "#3B4058": "正文/字段值", "#707589": "标签/次要文字",
        "#3183F5": "激活态Tab/状态标签", "#999999": "未激活Tab",
        "#409EFF": "按钮文字/蓝色条", "#F7F8FA": "商品组底色",
        "#FEF2EC": "配套头底色", "#F25C0C": "配套图标/标签橙色",
        "#000000": "底部指示器",
    }
    for color, count in cc.most_common(20):
        lines.append(f"| {color} | {count} | {usage.get(color, '—')} |")

    # 截图确认清单
    all_texts = _collect_all_texts(tree)
    lines.append("\n---\n## ⚠️ 截图确认清单")
    lines.append("| JSON 文字 | 截图实际文字 |")
    lines.append("|---|---|")
    seen = set()
    for t in all_texts:
        if t["text"] not in seen:
            seen.add(t["text"])
            lines.append(f"| `{t['text']}` | |")

    lines.append(f"\n---\n*由 design-analyzer/generate_spec.py 自动生成*")
    return "\n".join(lines)


def _render(node, lines, depth):
    h = ["##", "###", "####", "#####", "######"]
    level = h[min(depth, len(h) - 1)]
    name = node["name"]
    bn = node["bounds"]
    w, ht = bn.get("width", 0), bn.get("height", 0)

    flags = []
    fills = node.get("fills", [])
    if fills:
        flags.append(f"bg={','.join(fills)}")
    radius = node.get("radius", [])
    if radius:
        flags.append(f"r={radius}")
    borders = node.get("borders", 0)
    if borders:
        flags.append(f"border={borders}")
    repeat = node.get("repeat", 0)
    if repeat:
        flags.append(f"×{repeat}")

    flag_str = f" ({'; '.join(flags)})" if flags else ""
    indent = "  " * depth if depth > 2 else ""

    if depth <= 1:
        lines.append(f"\n{level} {name} — {w:.0f}×{ht:.0f}px{flag_str}")
    else:
        ntype = node.get("type", "")
        lines.append(f"\n{indent}{level} [{ntype}] {name} — {w:.0f}×{ht:.0f}px{flag_str}")

    for t in node.get("texts", []):
        fs = f"fs={t['fontSize']}" if t.get("fontSize") else ""
        fw = f"fw={t['fontWeight']}" if t.get("fontWeight") else ""
        fc = t.get("color", "")
        lines.append(f"{indent}  - ⚠️ `{t['text']}`  {fs} {fw}  {fc} |")

    children = merge_repeats(node.get("children", []))
    # 计算子元素间距（按 y 坐标排序）
    sorted_children = sorted(children, key=lambda c: c.get("bounds", {}).get("top", 0))
    if len(sorted_children) > 1:
        gaps = []
        for i in range(len(sorted_children) - 1):
            a_bn = sorted_children[i].get("bounds", {})
            b_bn = sorted_children[i + 1].get("bounds", {})
            a_bot = a_bn.get("top", 0) + a_bn.get("height", 0)
            b_top = b_bn.get("top", 0)
            g = round(b_top - a_bot, 1)
            if g > 0:
                a_name = sorted_children[i].get("name", "")
                b_name = sorted_children[i + 1].get("name", "")
                gaps.append((a_name, b_name, g))
        if gaps:
            gap_indent = "  " * max(depth, 2)
            lines.append(f"{gap_indent}- 子元素间距:")
            for a, bn, g in gaps[:10]:
                lines.append(f"{gap_indent}  - {a} → {bn}: {g}px ({px2rpx(g)}rpx)")

    for child in children:
        _render(child, lines, depth + 1)


def _collect_colors(node, cc):
    for c in node.get("fill", {}).get("colors", []):
        v = c.get("value", {})
        r, g, b = v.get("r", 0), v.get("g", 0), v.get("b", 0)
        if not (r > 248 and g > 248 and b > 248):
            cc[to_hex(v)] += 1
    # 也收集 text.styles 中的颜色
    for s in node.get("text", {}).get("styles", []):
        color_val = s.get("font", {}).get("color", {}).get("value", {})
        if color_val:
            r, g, b = color_val.get("r", 0), color_val.get("g", 0), color_val.get("b", 0)
            if not (r > 248 and g > 248 and b > 248):
                cc[to_hex(color_val)] += 1
    for child in node.get("children", []):
        _collect_colors(child, cc)


def _collect_all_texts(node):
    result = []
    for t in node.get("texts", []):
        result.append(t)
    for child in node.get("children", []):
        result.extend(_collect_all_texts(child))
    return result


def main():
    p = argparse.ArgumentParser(description="生成设计规格文档")
    p.add_argument("file", help="layerData.json 路径")
    p.add_argument("-o", "--output", help="输出文件路径（默认当前目录下 spec_页面名.md）")
    p.add_argument("-w", "--width", type=int, default=375, help="设计图宽度（默认 375px）")
    args = p.parse_args()

    with open(args.file) as f:
        root = json.load(f)[0]

    # 取页面名称，生成文件名
    page_name = root.get("basic", {}).get("name", "design")
    out_file = args.output or f"spec_{page_name}.md"

    md = generate(root, args.width)

    with open(out_file, "w") as f:
        f.write(md)
    print(f"✅ 规格文档已生成: {out_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
