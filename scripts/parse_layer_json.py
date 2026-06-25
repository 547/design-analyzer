#!/usr/bin/env python3
"""解析 摹客 / Figma 等设计工具导出的图层 JSON，输出每层的文字、色值、坐标、圆角，并自动计算间距、推测布局。

用法:
  python3 parse_layer_json.py layerData.json                           # 全文输出
  python3 parse_layer_json.py layerData.json --search 配套 价格 商品    # 关键词搜索
  python3 parse_layer_json.py layerData.json --range 500 700           # 坐标范围过滤
  python3 parse_layer_json.py layerData.json --summary                 # 仅输出摘要（组件树 + 颜色表）
"""

import json, sys, argparse
from collections import Counter


def to_hex(r, g, b):
    return f"#{round(r):02X}{round(g):02X}{round(b):02X}"


def text_info(node):
    """提取文字（支持 text.styles 和 text.characters 两种结构）"""
    text_data = node.get("text", {})
    chars = text_data.get("characters", "")
    style = text_data.get("style", {})
    styles = text_data.get("styles", [])

    # 优先用 styles[0].value（摹客/Figma 实际文字）
    if styles:
        s = styles[0]
        font = s.get("font", {})
        return {
            "text": s.get("value", ""),
            "fontSize": font.get("size") or font.get("fontSize"),
            "fontWeight": font.get("fontWeight") or font.get("weight"),
            "fills": [to_hex(font["color"]["value"]["r"], font["color"]["value"]["g"], font["color"]["value"]["b"])] if font.get("color", {}).get("value") else [],
            "radius": [],
        }

    # 回退到旧格式
    fills = node.get("fill", {}).get("colors", [])
    rlist = node.get("stroke", {}).get("radius", [])
    return {
        "text": chars,
        "fontSize": style.get("fontSize"),
        "fontWeight": style.get("fontWeight"),
        "fills": [to_hex(c["value"]["r"], c["value"]["g"], c["value"]["b"]) for c in fills] if fills else [],
        "radius": rlist,
    }


def calc_spacing(children):
    """计算相邻子元素间距（按 top 排序）"""
    sorted_c = sorted([c for c in children if "bounds" in c], key=lambda c: c["bounds"]["top"])
    spacings = []
    for i in range(len(sorted_c) - 1):
        a, b = sorted_c[i]["bounds"], sorted_c[i + 1]["bounds"]
        gap = round(b["top"] - (a["top"] + a.get("height", 0)), 1)
        if gap > 0:
            spacings.append({
                "from": sorted_c[i].get("basic", {}).get("name", ""),
                "to": sorted_c[i+1].get("basic", {}).get("name", ""),
                "gap": gap,
            })
    return spacings


def detect_layout(children):
    """推测布局：vertical / horizontal / mixed"""
    blist = [c["bounds"] for c in children if "bounds" in c]
    if len(blist) < 2:
        return ""
    tops = [b["top"] for b in blist]
    lefts = [b["left"] for b in blist]
    top_diffs = [tops[i+1] - tops[i] for i in range(len(tops)-1)]
    if all(d > 0 for d in top_diffs) and max(lefts) - min(lefts) < 20:
        return "↓ vertical"
    if all(b["left"] > blist[i-1]["left"] for i, b in enumerate(blist) if i > 0) and max(tops) - min(tops) < 20:
        return "→ horizontal"
    return "mixed"


def collect_colors(node, counter, depth=0):
    """全图颜色统计"""
    for c in node.get("fill", {}).get("colors", []):
        r, g, b = c["value"]["r"], c["value"]["g"], c["value"]["b"]
        if not (r > 248 and g > 248 and b > 248):
            counter[to_hex(r, g, b)] += 1
    for s in node.get("text", {}).get("styles", []):
        cv = s.get("font", {}).get("color", {}).get("value", {})
        if cv:
            r, g, b = cv.get("r", 0), cv.get("g", 0), cv.get("b", 0)
            if not (r > 248 and g > 248 and b > 248):
                counter[to_hex(r, g, b)] += 1
    for child in node.get("children", []):
        collect_colors(child, counter, depth + 1)


def print_tree(node, depth=0, search=None, top_range=None, show_spacing=True):
    """递归打印图层树"""
    b = node.get("bounds", {})
    name = node.get("basic", {}).get("name", "")
    rtype = node.get("basic", {}).get("realType", "")
    ti = text_info(node)
    children = node.get("children", [])
    t = b.get("top", -1)

    # 范围过滤（叶节点才过滤，父节点放行以免误杀子节点）
    if top_range and not children and (t < top_range[0] or t > top_range[1]):
        return

    combined = name + ti["text"]
    matched = not search or any(kw in combined for kw in search)

    if matched and rtype in ("TEXT", "FRAME", "RECTANGLE", "VECTOR", "GROUP"):
        indent = "  " * depth
        layout_hint = ""
        if rtype == "FRAME" and len(children) > 1:
            layout_hint = " " + detect_layout(children)
        print(f"{indent}[{rtype}]{layout_hint} {name}")

        if ti["text"]:
            s = f" 字号:{ti['fontSize']} 字重:{ti['fontWeight']}" if ti["fontSize"] else ""
            print(f"{indent}  (JSON)「{ti['text']}」{s}")
        if ti["fills"]:
            print(f"{indent}  色: {', '.join(ti['fills'])}")
        if ti["radius"]:
            print(f"{indent}  圆角: {ti['radius']}")
        print(f"{indent}  pos=({b.get('left')}, {b.get('top')}) size={b.get('width')}x{b.get('height')}")

    # 间距
    if show_spacing and matched and rtype == "FRAME" and len(children) > 1:
        spacings = calc_spacing(children)
        if spacings:
            print(f"{'  '*(depth+1)}间距:")
            for s in spacings:
                print(f"{'  '*(depth+2)}{s['from']} → {s['to']}: {s['gap']}px")

    for child in children:
        print_tree(child, depth + 1, search, top_range, show_spacing)


def print_summary(node, depth=0, max_depth=4):
    """摘要模式：组件树 + 布局推测"""
    name = node.get("basic", {}).get("name", "")
    rtype = node.get("basic", {}).get("realType", "")
    children = node.get("children", [])
    b = node.get("bounds", {})

    if rtype in ("FRAME", "GROUP", "TEXT") and depth <= max_depth:
        indent = "  " * depth
        text_nodes = [c for c in children
                      if c.get("basic", {}).get("realType", "") == "TEXT"
                      and c.get("text", {}).get("characters", "")]
        texts = [c["text"]["characters"] for c in text_nodes if c["text"]["characters"]]
        layout = detect_layout(children) if len(children) > 1 else ""
        extra = f" ({b.get('width')}x{b.get('height')})" if b else ""
        print(f"{indent}[{rtype}] {name}{extra}")
        if texts:
            print(f"{indent}  ⚠️文字需确认: {', '.join(texts[:8])}")
        if layout:
            print(f"{indent}  布局: {layout}")

    for child in children:
        print_summary(child, depth + 1, max_depth)


def main():
    p = argparse.ArgumentParser(description="解析摹客/Figma 设计图层 JSON")
    p.add_argument("file", help="layerData.json 路径")
    p.add_argument("--search", nargs="+", help="关键词搜索")
    p.add_argument("--range", nargs=2, type=float, help="top 坐标范围")
    p.add_argument("--summary", action="store_true", help="仅输出组件树 + 颜色表")
    p.add_argument("--no-spacing", action="store_true", help="不计算间距")
    args = p.parse_args()

    with open(args.file) as f:
        root = json.load(f)[0]

    top_range = tuple(args.range) if args.range else None

    if args.summary:
        print("=== 组件树 ===")
        print_summary(root)
        cc = Counter()
        collect_colors(root, cc)
        if cc:
            print("\n=== 颜色表 ===")
            for color, count in cc.most_common(15):
                print(f"  {color}: {count}次")
    else:
        print("=== 图层详情 === (颜色/坐标/字号可信; 文字需以截图为准)")
        print_tree(root, search=args.search, top_range=top_range,
                   show_spacing=not args.no_spacing)
        cc = Counter()
        collect_colors(root, cc)
        if cc:
            print("\n=== 颜色汇总 ===")
            for color, count in cc.most_common(10):
                print(f"  {color}: {count}次")


if __name__ == "__main__":
    main()
