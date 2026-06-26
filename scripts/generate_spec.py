#!/usr/bin/env python3
"""生成设计规格文档（Markdown）。

从摹客图层 JSON 提取：色值、坐标、字号、圆角、间距
自动推断：组件类型、布局方向、开发任务清单

用法:
  python3 generate_spec.py design.json           # 全量分析
  python3 generate_spec.py design.json --focus "配套 卡片"  # 部分修改（多关键词空格分隔）
"""

import json, argparse, sys
from collections import Counter

COMP_TYPE_ALIASES = {
    "卡片": "Card",
    "按钮": "Button",
    "导航": "Navigation",
    "导航栏": "Navigation",
    "列表项": "List Item",
    "输入框": "Input Field",
    "表单项": "Form Row",
    "弹窗": "Modal",
    "模态框": "Modal",
    "容器": "Container",
    "标题": "Heading",
    "图标": "Icon",
    "头像": "Avatar",
    "分割线": "Divider",
    "分隔线": "Divider",
    "圆角矩形": "Rounded Box",
}


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
    text_data = node.get("text", {})
    styles = text_data.get("styles", [])
    if not styles:
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
            "alignment": s.get("alignment"),
        })
    return result


def get_opacity(node):
    return node.get("opacity", 1)


def get_blur(node):
    return node.get("blur", {}).get("radius", 0)


# ---- 扁平列表 → 嵌套树 ----

def build_tree_from_flat_list(flat_nodes):
    """将摹客的扁平图层列表转换为嵌套树结构。
    
    摹客 JSON 是扁平的，根节点已有 children 数组，
    但子节点需要通过 parentId 建立完整的嵌套关系。
    """
    # 建立 id → node 映射
    node_map = {}
    for node in flat_nodes:
        nid = node.get("basic", {}).get("id", "")
        if nid:
            node_map[nid] = node

    # 为每个节点建立 children（如果还没有）
    for node in flat_nodes:
        if "children" not in node or not node.get("children"):
            node["children"] = []
    
    # 建立 parentId → children 映射
    children_map = {}
    root_nodes = []

    for node in flat_nodes:
        pid = node.get("parentId")
        if pid and pid in node_map:
            if pid not in children_map:
                children_map[pid] = []
            children_map[pid].append(node)
        else:
            # 没有 parentId 或 parentId 不在 node_map 中，视为根节点
            root_nodes.append(node)

    # 如果有多个根节点，创建一个虚拟根
    if len(root_nodes) > 1:
        virtual_root = {
            "basic": {"id": "__root__", "name": "Root", "realType": "FRAME"},
            "bounds": {"top": 0, "left": 0, "width": 0, "height": 0},
            "fill": {"colors": []},
            "stroke": {},
            "text": {},
            "opacity": 1,
            "blur": {},
            "children": root_nodes,
        }
        return virtual_root
    elif len(root_nodes) == 1:
        root = root_nodes[0]
        # 根节点已有 children，确保它被正确设置
        rid = root.get("basic", {}).get("id", "")
        if rid in children_map:
            root["children"] = children_map[rid]
        return root
    else:
        return flat_nodes[0] if flat_nodes else {}


# ---- 组件类型推断 ----

def infer_component_type(node, context=""):
    ntype = get_type(node)
    nname = get_name(node)
    fills = get_fill_colors(node)
    radius = get_radius(node)
    texts = get_text_info(node)
    bounds = get_bounds(node)
    children = node.get("children", [])
    child_types = [get_type(c) for c in children]

    name_lower = nname.lower()
    text_content = "".join(t["text"] for t in texts).lower()

    if ntype == "TEXT":
        if "标签" in nname or "badge" in name_lower:
            return "Label/Badge"
        if "标题" in nname or "title" in name_lower:
            return "Heading"
        if "描述" in nname or "desc" in name_lower or "description" in name_lower:
            return "Description"
        if "提示" in nname or "hint" in name_lower:
            return "Hint Text"
        return "Text"

    if ntype == "RECTANGLE":
        if radius:
            if bounds["width"] < 40 and bounds["height"] < 40:
                return "Icon"
            if bounds["width"] < 60:
                return "Avatar"
            return "Rounded Box"
        if bounds["width"] <= bounds["height"]:
            return "Divider"
        return "Rectangle"

    if ntype == "VECTOR":
        # 高度为 0 或极小的 Vector 通常是分隔线
        if bounds["height"] <= 1:
            return "Divider"
        return "Icon"

    if ntype in ("FRAME", "GROUP"):
        if "按钮" in nname or "button" in name_lower or "btn" in name_lower:
            return "Button"
        if "输入" in nname or "input" in name_lower or "field" in name_lower:
            return "Input Field"
        if "卡片" in nname or "card" in name_lower:
            return "Card"
        if "列表" in nname or "list" in name_lower or "cell" in name_lower:
            return "List Item"
        if "导航" in nname or "nav" in name_lower or "tab" in name_lower:
            return "Navigation"
        if "弹窗" in nname or "modal" in name_lower or "dialog" in name_lower:
            return "Modal"
        if "开关" in nname or "switch" in name_lower:
            return "Switch"
        if "滑块" in name_lower or "slider" in name_lower:
            return "Slider"
        if "进度" in nname or "progress" in name_lower:
            return "Progress Bar"
        if "复选" in nname or "checkbox" in name_lower:
            return "Checkbox"
        if "单选" in nname or "radio" in name_lower:
            return "Radio"
        
        if radius and fills:
            if texts and len(texts) <= 3:
                return "Button"
            return "Card"
        
        if "TEXT" in child_types and "RECTANGLE" in child_types:
            if len(children) <= 5:
                return "Form Row"
        
        if all(c == "TEXT" for c in child_types):
            return "Text Block"
        
        if len(children) > 5 and "TEXT" in child_types:
            return "List Container"
        
        return "Container"

    return ntype


# ---- 布局分析 ----

def detect_layout(children):
    blist = [get_bounds(c) for c in children if get_bounds(c)]
    if len(blist) < 2:
        return {"type": "single", "direction": ""}
    
    tops = [b["top"] for b in blist]
    lefts = [b["left"] for b in blist]
    widths = [b["width"] for b in blist]
    heights = [b["height"] for b in blist]
    
    sorted_by_top = sorted(blist, key=lambda b: b["top"])
    sorted_by_left = sorted(blist, key=lambda b: b["left"])
    
    vertical_gaps = []
    for i in range(len(sorted_by_top) - 1):
        a, b = sorted_by_top[i], sorted_by_top[i+1]
        gap = b["top"] - (a["top"] + a["height"])
        if gap >= 0:
            vertical_gaps.append(gap)
    
    horizontal_gaps = []
    for i in range(len(sorted_by_left) - 1):
        a, b = sorted_by_left[i], sorted_by_left[i+1]
        gap = b["left"] - (a["left"] + a["width"])
        if gap >= 0:
            horizontal_gaps.append(gap)
    
    top_variance = max(tops) - min(tops)
    left_variance = max(lefts) - min(lefts)
    
    avg_width = sum(widths) / len(widths)
    avg_height = sum(heights) / len(heights)
    
    is_grid = False
    if len(children) >= 4:
        row_count = 1
        for i in range(1, len(sorted_by_top)):
            if sorted_by_top[i]["top"] - sorted_by_top[i-1]["top"] > avg_height * 0.5:
                row_count += 1
        col_count = len(children) // row_count if row_count > 0 else 1
        if row_count >= 2 and col_count >= 2:
            is_grid = True
    
    if is_grid:
        return {"type": "grid", "direction": f"{col_count}×{row_count}", "vertical_gaps": vertical_gaps, "horizontal_gaps": horizontal_gaps}
    
    if top_variance > left_variance * 1.5:
        return {"type": "vertical", "direction": "↓", "vertical_gaps": vertical_gaps}
    
    if left_variance > top_variance * 1.5:
        return {"type": "horizontal", "direction": "→", "horizontal_gaps": horizontal_gaps}
    
    return {"type": "mixed", "direction": "↕", "vertical_gaps": vertical_gaps, "horizontal_gaps": horizontal_gaps}


# ---- 树结构 ----

def has_content(node):
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
    ntype = get_type(node)
    nname = get_name(node)
    bn = get_bounds(node)
    
    comp_type = infer_component_type(node, nname)

    if ntype == "TEXT":
        ti = get_text_info(node)
        if ti:
            return {"type": ntype, "name": nname, "bounds": bn, "texts": ti, "children": [], "component": comp_type}
        return None

    if ntype not in ("FRAME", "GROUP"):
        fills = get_fill_colors(node)
        radius = get_radius(node)
        borders = get_border_count(node)
        if fills or radius or borders:
            # 提取图标名称（从节点名中推断）
            icon_name = ""
            if ntype in ("VECTOR", "BITMAP"):
                bw = bn.get("width", 0)
                bh = bn.get("height", 0)
                # 小尺寸单色 Vector/Bitmap 视为图标
                if bw <= 44 and bh <= 44 and len(fills) <= 1 and borders <= 1:
                    # 保留原始名称（如 "Cap", "路径 2" 等），方便对应切图文件
                    icon_name = nname
            
            return {
                "type": ntype, "name": nname, "bounds": bn,
                "fills": fills, "radius": radius, "borders": borders,
                "texts": [], "children": [], "component": comp_type,
                "icon_name": icon_name,
            }
        return None

    fills = get_fill_colors(node)
    radius = get_radius(node)
    borders = get_border_count(node)
    opacity = get_opacity(node)
    blur = get_blur(node)

    children = []
    for child in node.get("children", []):
        sub = build_tree(child, depth + 1)
        if sub:
            children.append(sub)

    # 检测图标容器：小尺寸 Frame（≤24×24）包含纯 Vector 子节点
    icon_name = ""
    if ntype == "FRAME" and children and not fills:
        bw = bn.get("width", 0)
        bh = bn.get("height", 0)
        if bw <= 24 and bh <= 24 and all(c["type"] == "VECTOR" for c in children):
            # 检查子节点颜色是否一致
            child_colors = set()
            for c in children:
                if c.get("fills"):
                    child_colors.add(c["fills"][0])
                if c.get("borders"):
                    child_colors.add(f"border:{c['borders']}")
            if len(child_colors) <= 2:  # 最多两种颜色（fill + stroke）
                # 从名称提取图标名（保留原始名称如 "Frame"）
                icon_name = nname

    if children and all(c["type"] == "TEXT" for c in children):
        texts = []
        for c in children:
            texts.extend(c.get("texts", []))
        tcolors = []
        for c in children:
            for t in c.get("texts", []):
                if t.get("color"):
                    tcolors.append(t["color"])
        return {
            "type": ntype, "name": nname, "bounds": bn,
            "fills": fills or tcolors, "radius": radius, "borders": borders,
            "opacity": opacity, "blur": blur,
            "texts": texts, "children": [], "component": comp_type,
        }

    layout_info = detect_layout(children)

    return {
        "type": ntype, "name": nname, "bounds": bn,
        "fills": fills, "radius": radius, "borders": borders,
        "opacity": opacity, "blur": blur,
        "texts": [], "children": children, "component": comp_type,
        "layout": layout_info,
        "icon_name": icon_name,
    }


def merge_repeats(children):
    if len(children) < 2:
        return children

    result = []
    i = 0
    while i < len(children):
        cur = children[i]
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
    parts = [node.get("type", ""), node.get("component", "")]
    for t in node.get("texts", []):
        parts.append(t.get("text", ""))
    for f in node.get("fills", []):
        parts.append(f)
    r = node.get("radius", [])
    if r:
        parts.append(f"r{r}")
    parts.append(f"c{len(node.get('children', []))}")
    return tuple(parts) if len(parts) > 2 else None


# ---- 样式汇总 ----

def collect_font_styles(node, counter):
    for s in node.get("text", {}).get("styles", []):
        font = s.get("font", {})
        size = font.get("size") or font.get("fontSize")
        weight = font.get("fontWeight") or font.get("weight")
        color_val = font.get("color", {}).get("value", {})
        if size:
            color_hex = to_hex(color_val) if color_val else ""
            key = f"{size}px/{weight or 'normal'}/{color_hex}"
            counter[key] += 1
    for child in node.get("children", []):
        collect_font_styles(child, counter)


def collect_spacing_patterns(node, patterns):
    children = node.get("children", [])
    if len(children) < 2:
        return
    
    layout_info = detect_layout(children)
    if layout_info["type"] == "vertical" and layout_info["vertical_gaps"]:
        for gap in layout_info["vertical_gaps"]:
            patterns["vertical"].append(round(gap, 1))
    elif layout_info["type"] == "horizontal" and layout_info["horizontal_gaps"]:
        for gap in layout_info["horizontal_gaps"]:
            patterns["horizontal"].append(round(gap, 1))
    
    for child in children:
        collect_spacing_patterns(child, patterns)


# ---- 开发任务生成 ----

def _comp_type_matches(kw, comp_type):
    kw_lower = kw.lower()
    comp_lower = comp_type.lower()
    if kw_lower in comp_lower:
        return True
    for alias, eng in COMP_TYPE_ALIASES.items():
        if kw_lower in alias.lower() and eng.lower() == comp_lower:
            return True
    return False

def _node_has_match(node, focus_keywords):
    if not focus_keywords:
        return True
    name = node.get("name", "").lower()
    comp = node.get("component", "")
    for kw in focus_keywords:
        matched = False
        if kw in name:
            matched = True
        elif _comp_type_matches(kw, comp):
            matched = True
        else:
            for t in node.get("texts", []):
                if kw in t.get("text", "").lower():
                    matched = True
                    break
        if not matched:
            for child in node.get("children", []):
                if _node_has_match(child, [kw]):
                    matched = True
                    break
        if not matched:
            return False
    return True

def generate_development_tasks(tree, focus=None):
    tasks = []
    
    def collect_texts_recursive(node):
        all_texts = []
        for t in node.get("texts", []):
            all_texts.append(t.get("text", ""))
        for child in node.get("children", []):
            all_texts.extend(collect_texts_recursive(child))
        return all_texts
    
    focus_keywords = [k.strip().lower() for k in focus.split()] if focus else []
    
    def is_focus_match(name, path, comp_type, node=None):
        if not focus_keywords:
            return True
        name_lower = name.lower()
        path_lower = path.lower()
        all_texts = collect_texts_recursive(node) if node else []
        for kw in focus_keywords:
            matched = False
            if kw in name_lower or kw in path_lower:
                matched = True
            elif _comp_type_matches(kw, comp_type):
                matched = True
            elif node:
                for t in all_texts:
                    if kw in t.lower():
                        matched = True
                        break
            if not matched:
                return False
        return True
    
    def traverse(node, parent_path="", depth=0):
        comp_type = node.get("component", "")
        nname = node.get("name", "")
        bounds = node.get("bounds", {})
        fills = node.get("fills", [])
        radius = node.get("radius", [])
        texts = node.get("texts", [])
        
        path = f"{parent_path} > {nname}" if parent_path else nname
        
        if comp_type in ("Button", "Card", "List Item", "Input Field", "Navigation", "Modal", "Container", "Form Row"):
            if is_focus_match(nname, path, comp_type, node):
                has_matching_child = False
                for child in node.get("children", []):
                    if _node_has_match(child, focus_keywords):
                        has_matching_child = True
                        break
                if not focus_keywords or not has_matching_child or comp_type != "Container":
                    task = {
                        "component": comp_type,
                        "name": nname,
                        "path": path,
                        "size": f"{bounds['width']:.0f}×{bounds['height']:.0f}px",
                        "style": [],
                        "text_count": len(texts),
                        "children_count": len(node.get("children", [])),
                        "priority": "high" if depth <= 2 else "medium",
                    }
                    if fills:
                        task["style"].append(f"background: {','.join(fills)}")
                    if radius:
                        task["style"].append(f"border-radius: {radius}")
                    tasks.append(task)
        
        for child in node.get("children", []):
            traverse(child, path, depth + 1)
    
    traverse(tree)
    return tasks


# ---- 文档生成 ----

def generate(root, design_width=375, focus=None):
    tree = build_tree(root)
    if not tree:
        tree = {"type": "FRAME", "name": "Root", "bounds": get_bounds(root),
                "fills": get_fill_colors(root), "texts": [], "children": [], "component": "Container"}

    lines = []
    lines.append("# 设计规格文档\n")
    bn = get_bounds(root)
    fills = get_fill_colors(root)
    bg = fills[0] if fills else "?"
    lines.append(f"**页面**: {bn['width']:.0f}×{bn['height']:.0f}px | **背景**: {bg} | rpx: 1px≈{px2rpx(1)}rpx")
    if focus:
        lines.append(f"**聚焦**: `{focus}`\n")
    else:
        lines.append("")

    _render(tree, lines, 0)

    # 颜色表
    cc = Counter()
    _collect_colors(root, cc)
    lines.append("\n---\n## 颜色表")
    lines.append("| 色值 | 次数 | 推测用途 |")
    lines.append("|---|---|---|")
    usage = {
        "#F2F2F2": "页面背景", "#FFFFFF": "卡片/行底色", "#FAFAFA": "页面背景",
        "#F5F5F5": "次要背景", "#3975C6": "导航栏", "#2C2C2C": "页面标题",
        "#3B4058": "正文/字段值", "#707589": "标签/次要文字", "#666666": "次要文字",
        "#3183F5": "激活态Tab/状态标签", "#999999": "未激活Tab/占位文字",
        "#409EFF": "按钮文字/蓝色条", "#F7F8FA": "商品组底色", "#FEF2EC": "配套头底色",
        "#F25C0C": "配套图标/标签橙色", "#FF6600": "强调色/价格", "#FF5722": "强调色",
        "#FF4D4F": "错误/警告", "#52C41A": "成功/完成", "#000000": "底部指示器",
        "#333333": "重要文字", "#1A1A1A": "标题文字",
    }
    
    def guess_usage(color):
        if color in usage:
            return usage[color]
        hex_str = color.lstrip('#')
        r, g, b = int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)
        brightness = (r * 299 + g * 587 + b * 114) / 1000
        if brightness > 240:
            return "浅色背景"
        if brightness < 50:
            return "深色文字/背景"
        if r > 200 and g < 100 and b < 100:
            return "红色系（错误/警告）"
        if r < 100 and g > 150 and b < 100:
            return "绿色系（成功）"
        if r < 100 and g < 150 and b > 200:
            return "蓝色系（主色/链接）"
        if r > 200 and g > 150 and b < 100:
            return "橙色系（强调/价格）"
        return "—"
    
    for color, count in cc.most_common(20):
        lines.append(f"| {color} | {count} | {guess_usage(color)} |")

    # 字体样式表
    fc = Counter()
    collect_font_styles(root, fc)
    if fc:
        lines.append("\n---\n## 字体样式表")
        lines.append("| 字号/字重/颜色 | 出现次数 |")
        lines.append("|---|---|")
        for style, count in fc.most_common(15):
            lines.append(f"| {style} | {count} |")

    # 间距模式
    spacing_patterns = {"vertical": [], "horizontal": []}
    collect_spacing_patterns(root, spacing_patterns)
    if spacing_patterns["vertical"] or spacing_patterns["horizontal"]:
        lines.append("\n---\n## 间距模式")
        if spacing_patterns["vertical"]:
            v_counter = Counter(spacing_patterns["vertical"])
            lines.append("### 垂直间距")
            for gap, count in v_counter.most_common(8):
                lines.append(f"  - {gap}px: {count}次 ({px2rpx(gap)}rpx)")
        if spacing_patterns["horizontal"]:
            h_counter = Counter(spacing_patterns["horizontal"])
            lines.append("\n### 水平间距")
            for gap, count in h_counter.most_common(8):
                lines.append(f"  - {gap}px: {count}次 ({px2rpx(gap)}rpx)")

    # 可复用组件分析
    reusable = _analyze_reusable_components(tree)
    if reusable:
        lines.append("\n---\n## 可复用组件")
        lines.append("以下组件在页面中多次出现，结构相同，建议提取为独立组件：\n")
        for i, group in enumerate(reusable, 1):
            lines.append(f"### {i}. {group['name']}（出现 {group['count']} 次）")
            lines.append(f"- **类型**: {group['component']}")
            lines.append(f"- **尺寸**: {group['size']}")
            lines.append(f"- **背景色**: {group['bg']}")
            if group.get("radius"):
                lines.append(f"- **圆角**: {group['radius']}")
            if group.get("border"):
                lines.append(f"- **边框**: {group['border']}")
            lines.append(f"- **子元素**: {group['children_desc']}")
            lines.append(f"- **使用位置**: {', '.join(group['locations'][:5])}")
            if group['count'] > 5:
                lines.append(f"  - ... 等 {group['count']} 处")
            lines.append("")

    # 开发任务清单
    tasks = generate_development_tasks(tree, focus=focus)
    if tasks:
        if focus:
            lines.append(f"\n---\n## 📋 开发任务清单（聚焦: {focus}）")
        else:
            lines.append("\n---\n## 📋 开发任务清单")
        lines.append("| 优先级 | 组件类型 | 名称 | 尺寸 | 样式要点 |")
        lines.append("|---|---|---|---|---|")
        for task in sorted(tasks, key=lambda t: {"high": 0, "medium": 1, "low": 2}[t["priority"]]):
            style_str = ", ".join(task["style"]) if task["style"] else "—"
            lines.append(f"| {task['priority'].upper()} | {task['component']} | {task['name']} | {task['size']} | {style_str} |")

    # 文字清单
    all_texts = _collect_all_texts(tree)
    if all_texts:
        lines.append("\n---\n## 文字清单")
        seen = set()
        for t in all_texts:
            if t["text"] not in seen and t["text"]:
                seen.add(t["text"])
                lines.append(f"- `{t['text']}`")

    # 切图对应表
    icons = _collect_all_icons(tree)
    if icons:
        # 去重：按图标名称去重
        seen_icons = {}
        for icon in icons:
            key = icon["name"]
            if key not in seen_icons:
                seen_icons[key] = icon
        lines.append("\n---\n## 切图对应表")
        lines.append("| 图标名称 | 尺寸 | 颜色 | 切图文件 | 使用位置 |")
        lines.append("|---|---|---|---|---|")
        for icon in seen_icons.values():
            svg_name = _guess_svg_name(icon["name"])
            lines.append(f"| {icon['name']} | {icon['size']} | {icon['color']} | {svg_name} | {icon['location']} |")

    # 实现要点
    impl_notes = _generate_impl_notes(tree)
    if impl_notes:
        # 去重
        seen_notes = set()
        unique_notes = []
        for note in impl_notes:
            if note not in seen_notes:
                seen_notes.add(note)
                unique_notes.append(note)
        if unique_notes:
            lines.append("\n---\n## 实现要点")
            for note in unique_notes:
                lines.append(f"- {note}")

    lines.append(f"\n---\n*由 design-analyzer 自动生成*")
    return "\n".join(lines)


def _render(node, lines, depth):
    h = ["##", "###", "####", "#####", "######"]
    level = h[min(depth, len(h) - 1)]
    name = node["name"]
    bn = node["bounds"]
    w, ht = bn.get("width", 0), bn.get("height", 0)
    comp_type = node.get("component", "")

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
    opacity = node.get("opacity", 1)
    if opacity < 1:
        flags.append(f"opacity={opacity}")
    blur = node.get("blur", 0)
    if blur:
        flags.append(f"blur={blur}")
    repeat = node.get("repeat", 0)
    if repeat:
        flags.append(f"×{repeat}")
    layout = node.get("layout", {})
    if layout.get("type") and layout.get("type") != "single":
        flags.append(f"{layout['type']} {layout.get('direction', '')}")

    flag_str = f" ({'; '.join(flags)})" if flags else ""
    indent = "  " * depth if depth > 2 else ""

    if depth <= 1:
        lines.append(f"\n{level} [{comp_type}] {name} — {w:.0f}×{ht:.0f}px{flag_str}")
    else:
        ntype = node.get("type", "")
        lines.append(f"\n{indent}{level} [{comp_type}] {name} — {w:.0f}×{ht:.0f}px{flag_str}")

    # 输出图标名称（如果有）
    icon_name = node.get("icon_name", "")
    if icon_name:
        lines.append(f"{indent}  - [图标: {icon_name}]")

    for t in node.get("texts", []):
        fs = f"fs={t['fontSize']}" if t.get("fontSize") else ""
        fw = f"fw={t['fontWeight']}" if t.get("fontWeight") else ""
        fc = t.get("color", "")
        lh = f"lh={t['lineHeight']}" if t.get("lineHeight") else ""
        alignment = t.get("alignment", "")
        align_str = f"align={alignment}" if alignment else ""
        extra = " ".join(filter(None, [fs, fw, lh, align_str]))
        lines.append(f"{indent}  - `{t['text']}`  {extra}  {fc}")

    children = merge_repeats(node.get("children", []))
    # Sort based on layout direction: horizontal layouts sort by left, vertical by top
    layout = node.get("layout", {})
    if layout.get("type") == "horizontal":
        sorted_children = sorted(children, key=lambda c: c.get("bounds", {}).get("left", 0))
    elif layout.get("type") == "grid":
        # Grid: sort by row (top) then column (left)
        sorted_children = sorted(children, key=lambda c: (c.get("bounds", {}).get("top", 0), c.get("bounds", {}).get("left", 0)))
    else:
        # Vertical or mixed: sort by top
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

    for child in sorted_children:
        _render(child, lines, depth + 1)


def _collect_colors(node, cc):
    for c in node.get("fill", {}).get("colors", []):
        v = c.get("value", {})
        r, g, b = v.get("r", 0), v.get("g", 0), v.get("b", 0)
        if not (r > 248 and g > 248 and b > 248):
            cc[to_hex(v)] += 1
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


def _collect_components(node, components):
    comp_type = node.get("component", "")
    if comp_type in ("Button", "Card", "List Item", "Input Field", "Navigation", "Modal"):
        bn = node.get("bounds", {})
        components.append({
            "component": comp_type,
            "name": node.get("name", ""),
            "size": f"{bn['width']:.0f}×{bn['height']:.0f}px",
        })
    for child in node.get("children", []):
        _collect_components(child, components)


def _collect_all_icons(node, parent_name="", icons=None):
    """收集所有图标信息，包括名称、尺寸、颜色、使用位置。"""
    if icons is None:
        icons = []
    icon_name = node.get("icon_name", "")
    if icon_name:
        fills = node.get("fills", [])
        borders = node.get("borders", 0)
        # 对于 Frame 容器图标，收集子节点的颜色
        color = fills[0] if fills else ""
        if not color and node.get("children"):
            child_colors = set()
            for c in node.get("children", []):
                cf = c.get("fills", [])
                if cf:
                    child_colors.add(cf[0])
            if len(child_colors) == 1:
                color = child_colors.pop()
            elif child_colors:
                color = ",".join(sorted(child_colors))
        if not color and borders:
            color = "stroke"
        if not color:
            color = "—"
        bn = node.get("bounds", {})
        size = f"{bn.get('width', 0):.0f}×{bn.get('height', 0):.0f}"
        location = parent_name if parent_name else node.get("name", "")
        icons.append({
            "name": icon_name,
            "size": size,
            "color": color,
            "location": location,
        })
    for child in node.get("children", []):
        child_name = node.get("name", "")
        _collect_all_icons(child, child_name, icons)
    return icons


def _guess_svg_name(icon_name):
    """根据图标名称推测对应的 SVG 文件名。"""
    name_map = {
        "路径 2": "fanhui.svg",
        "Cap": "编组 7.svg",
        "Frame": "Frame.svg",
    }
    if icon_name in name_map:
        return name_map[icon_name]
    # 尝试模糊匹配
    name_lower = icon_name.lower()
    if "返回" in icon_name or "fanhui" in name_lower or "back" in name_lower:
        return "fanhui.svg"
    if "更多" in icon_name or "more" in name_lower or "cap" in name_lower:
        return "编组 7.svg"
    if "配套" in icon_name or "frame" in name_lower:
        return "Frame.svg"
    return f"{icon_name}.svg"


def _generate_impl_notes(tree):
    """生成实现要点，帮助开发者快速准确地实现设计稿。"""
    notes = []
    
    def analyze(node, depth=0):
        comp_type = node.get("component", "")
        nname = node.get("name", "")
        fills = node.get("fills", [])
        radius = node.get("radius", [])
        borders = node.get("borders", 0)
        children = node.get("children", [])
        layout = node.get("layout", {})
        bn = node.get("bounds", {})
        texts = node.get("texts", [])
        
        # 检测 info-row 类型（Form Row 或 Text Block 的字段行）
        if comp_type in ("Form Row", "Text Block") and len(children) >= 2:
            # 检查是否有分隔线
            has_divider = any(
                c.get("component") == "Divider" or c.get("type") == "RECTANGLE"
                for c in children
            )
            if not has_divider:
                # 检查是否是审批信息区的字段行（多个 Text Block 垂直排列）
                if comp_type == "Text Block" or "标题" not in nname:
                    notes.append(f"**信息字段行**：字段行之间**没有分隔线**，不要用 border-bottom")
        
        # 检测导航栏和 Tab 的关系
        if comp_type == "Navigation" and "tab" in nname.lower():
            if not borders and not radius:
                notes.append(f"**Tab 导航**：Tab 栏底部**没有分隔线**，与下方内容区直接衔接")
        
        # 检测配套卡片
        if "配套" in nname or (comp_type == "Card" and fills and "#FEF2EC" in fills):
            notes.append(f"**配套卡片**：只有头部 header 背景色为 `#FEF2EC`，下方商品列表背景色为 `#F7F8FA`")
            notes.append(f"**配套卡片**：商品列表复用普通商品卡片样式，卡片之间用**虚线分隔**，商品卡片**没有圆角**")
            notes.append(f"**配套卡片**：整体有圆角（`border-radius: 16rpx`），`overflow: hidden` 裁剪子元素")
        
        # 检测白色背景的大区块（可能是信息区或商品区）
        if comp_type == "Container" and fills and "#FFFFFF" in fills:
            w = bn.get("width", 0)
            h = bn.get("height", 0)
            if w >= 343 and h >= 400:
                notes.append(f"**白色区块**（`{nname or 'Container'}`）：背景色为 `#FFFFFF`，与页面背景 `#F2F2F2` 区分")
        
        # 检测 Vector 分隔线
        for child in children:
            if child.get("component") == "Divider" and child.get("type") == "VECTOR":
                c_bn = child.get("bounds", {})
                if c_bn.get("height", 0) <= 1:
                    notes.append(f"**分隔线**：`{child.get('name', 'Vector')}` 是 1px 高度的分隔线，注意与 1px 间距区分")
        
        for child in children:
            analyze(child, depth + 1)
    
    analyze(tree)
    
    # 添加通用要点
    if not notes:
        notes.append("请严格按照规格文档中的色值、字号、间距实现")
    
    return notes


def _analyze_reusable_components(tree):
    """分析页面中可复用的组件，返回结构相同且出现多次的组件列表。"""
    # 收集所有候选组件（Form Row, Text Block, Container 等有子元素的节点）
    candidates = []
    
    def collect(node, path=""):
        comp_type = node.get("component", "")
        nname = node.get("name", "")
        children = node.get("children", [])
        bn = node.get("bounds", {})
        fills = node.get("fills", [])
        radius = node.get("radius", [])
        borders = node.get("borders", 0)
        
        current_path = f"{path} > {nname}" if path else nname
        
        # 只分析有子元素的组件
        if len(children) >= 2 and comp_type in ("Form Row", "Text Block", "Container", "Card"):
            # 生成结构签名：子组件类型 + 文字内容 + 布局方向
            child_sigs = []
            for c in children:
                c_type = c.get("component", c.get("type", ""))
                c_texts = [t.get("text", "") for t in c.get("texts", [])]
                child_sigs.append(f"{c_type}:{'|'.join(c_texts[:2])}")
            layout = node.get("layout", {})
            layout_type = layout.get("type", "")
            
            # 签名 = 子组件类型序列 + 布局方向 + 尺寸（宽高比例）
            w = bn.get("width", 0)
            h = bn.get("height", 0)
            sig = (
                "|".join(child_sigs),
                layout_type,
                round(w / max(h, 1), 2),  # 宽高比
                len(fills),
                len(radius) > 0,
                borders > 0,
            )
            
            candidates.append({
                "sig": sig,
                "name": nname,
                "component": comp_type,
                "size": f"{w:.0f}×{h:.0f}px",
                "bg": ",".join(fills) if fills else "transparent",
                "radius": str(radius) if radius else None,
                "border": f"{borders}px" if borders else None,
                "children_desc": " → ".join(child_sigs),
                "path": current_path,
                "node": node,
            })
        
        for child in children:
            collect(child, current_path)
    
    collect(tree)
    
    # 按签名分组
    groups = {}
    for c in candidates:
        sig = c["sig"]
        if sig not in groups:
            groups[sig] = []
        groups[sig].append(c)
    
    # 筛选出现 >= 2 次的组
    reusable = []
    for sig, items in groups.items():
        if len(items) >= 2:
            # 取第一个作为代表
            rep = items[0]
            reusable.append({
                "name": rep["name"],
                "component": rep["component"],
                "size": rep["size"],
                "bg": rep["bg"],
                "radius": rep["radius"],
                "border": rep["border"],
                "children_desc": rep["children_desc"],
                "count": len(items),
                "locations": [item["path"] for item in items],
            })
    
    # 按出现次数降序
    reusable.sort(key=lambda x: x["count"], reverse=True)
    return reusable


def main():
    p = argparse.ArgumentParser(description="生成设计规格文档")
    p.add_argument("file", help="摹客 JSON 文件路径")
    p.add_argument("-o", "--output", help="输出文件路径（默认当前目录下 spec_页面名.md）")
    p.add_argument("-w", "--width", type=int, default=375, help="设计图宽度（默认 375px）")
    p.add_argument("--focus", help="聚焦指定组件/区域（用于部分修改场景，匹配名称或路径）")
    args = p.parse_args()

    with open(args.file) as f:
        flat_nodes = json.load(f)

    # 摹客 JSON 是扁平列表，需要转换为嵌套树
    root = build_tree_from_flat_list(flat_nodes)

    page_name = root.get("basic", {}).get("name", "design")
    out_file = args.output or f"spec_{page_name}.md"

    md = generate(root, args.width, focus=args.focus)

    with open(out_file, "w") as f:
        f.write(md)
    print(f"✅ 规格文档已生成: {out_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
