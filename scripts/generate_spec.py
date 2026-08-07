#!/usr/bin/env python3
"""生成设计规格文档（Markdown）。

从摹客图层 JSON 提取：色值、坐标、字号、圆角、间距
自动推断：组件类型、布局方向、业务语义、实现要点
默认输出「页面结构（树形）+ 实现要点」两节，去除可从结构树推导的冗余统计

用法:
  python3 generate_spec.py design.json           # 全量分析
"""

import json, argparse, sys, os


def to_hex(rgb):
    # 兼容缺字段/越界值（clamp 到 0-255），不崩溃；保留 alpha 通道
    def _ch(k):
        v = rgb.get(k, 0)
        try:
            v = int(round(v))
        except (TypeError, ValueError):
            v = 0
        return max(0, min(255, v))
    r, g, b = _ch("r"), _ch("g"), _ch("b")
    a = rgb.get("a", 1)
    try:
        a = float(a)
    except (TypeError, ValueError):
        a = 1
    if a < 1:
        return f"rgba({r},{g},{b},{round(a, 2)})"
    return f"#{r:02X}{g:02X}{b:02X}"


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
    if not r or r in ([0], [0, 0, 0, 0]):
        return []
    # 收敛浮点噪声（如 2.6666667 → 2.7），整型保持整型
    clean = []
    for v in r:
        if isinstance(v, float):
            rv = round(v, 1)
            clean.append(int(rv) if rv == int(rv) else rv)
        else:
            clean.append(v)
    return clean


def get_border_count(node):
    return len(node.get("stroke", {}).get("borders", []))


def get_border_info(node):
    """返回边框详情列表，每项形如 '1px #EAE9E9'（宽度 + 颜色），供内联输出。"""
    out = []
    for b in node.get("stroke", {}).get("borders", []):
        w = b.get("strokeWidth", 1)
        c = to_hex(b.get("color", {}).get("value", {}))
        out.append(f"{w}px {c}")
    return out


def get_dash(node):
    """返回描边虚线样式；空列表/None 表示实线。"""
    d = node.get("stroke", {}).get("dash", [])
    return d if d else []


_ALIGN_MAP = {"LEFT": "左", "CENTER": "居中", "RIGHT": "右", "JUSTIFY": "两端"}


def _map_align(raw):
    """摹客 JSON 文字对齐字段名为 align（值 LEFT/CENTER/RIGHT），映射为中文。"""
    if not raw:
        return ""
    return _ALIGN_MAP.get(str(raw).upper(), "")


def get_text_info(node):
    text_data = node.get("text") or {}
    styles = text_data.get("styles", [])
    # 文字几何位置（用于对齐推断）：一个 TEXT 节点通常对应一个 bounds
    nb = get_bounds(node)
    if not styles:
        chars = text_data.get("characters", "")
        if chars:
            return [{
                "text": chars,
                "fontSize": None,
                "fontWeight": None,
                "color": "",
                "_bounds": nb,
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
            "alignment": _map_align(s.get("align")),
            "_bounds": nb,
        })
    return result


def get_opacity(node):
    # 摹客 JSON 透明度在 basic.opacity，不在顶层；缺字段/非数值时回退 1（不透明）
    try:
        return float(node.get("basic", {}).get("opacity", 1))
    except (TypeError, ValueError):
        return 1


def get_autolayout(node):
    """返回归一化的 autoLayout 信息（摹客权威尺寸/padding 来源）。

    字段：sizing_h / sizing_v（HUG/FIXED）、layout_mode（HORIZONTAL/VERTICAL）、
    padding（{left,top,right,bottom}，缺失键按 0）、item_spacing（子元素间距）。
    节点无 autoLayout 时返回 None（调用方回退坐标法）。
    """
    al = node.get("autolayout") or node.get("autoLayout")
    if not isinstance(al, dict):
        return None
    # 兼容已归一化格式（含 sizing_h 键，由 build_tree 预存）与原始 autoLayout 格式
    if "sizing_h" in al:
        return al
    if "layoutSizingHorizontal" not in al:
        return None
    has_pad = any(k in al for k in
                  ("paddingLeft", "paddingTop", "paddingRight", "paddingBottom"))
    pad = {
        "left": al.get("paddingLeft", 0) or 0,
        "top": al.get("paddingTop", 0) or 0,
        "right": al.get("paddingRight", 0) or 0,
        "bottom": al.get("paddingBottom", 0) or 0,
    } if has_pad else None  # 精简版无 padding 字段：不伪造为 0，交由坐标法回退
    return {
        "sizing_h": al.get("layoutSizingHorizontal", ""),
        "sizing_v": al.get("layoutSizingVertical", ""),
        "layout_mode": al.get("layoutMode", ""),
        "padding": pad,
        "item_spacing": al.get("itemSpacing", 0) or 0,
    }


def get_blur(node):
    return node.get("blur", {}).get("radius", 0)



# ---- 扁平列表 → 嵌套树 ----

def _get_parent_id(node):
    """兼容 parentId 位于顶层或 basic 下的两种摹客导出格式。"""
    pid = node.get("parentId")
    if pid is not None:
        return pid
    return node.get("basic", {}).get("parentId")


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
        pid = _get_parent_id(node)
        if pid and pid in node_map:
            if pid not in children_map:
                children_map[pid] = []
            children_map[pid].append(node)
        else:
            # 没有 parentId 或 parentId 不在 node_map 中，视为根节点
            root_nodes.append(node)

    # 把每个节点的 children 填充为其在 children_map 中的子节点，
    # 否则只有根节点会被赋值、嵌套子节点全部丢失（导致深层结构与间距缺失）。
    # 仅当节点原本没有 children 时才覆盖：若节点自带嵌套 children（如摹客导出
    # 的 children 未重复入 flat 列表），保留其原有结构，避免被清空。
    for node in flat_nodes:
        nid = node.get("basic", {}).get("id", "")
        if nid in children_map and not node.get("children"):
            node["children"] = children_map[nid]

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

def infer_component_type(node):
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

def detect_layout(children, node=None):
    blist = [get_bounds(c) for c in children if get_bounds(c)]
    if len(blist) < 2:
        # 子节点不足（如 TEXT 被内联为文字行、仅 1 个内容子节点）无法靠坐标判方向，
        # 回退用摹客 autoLayout 权威 layoutMode（HORIZONTAL/VERTICAL）。
        al = get_autolayout(node) if node is not None else None
        mode = (al or {}).get("layout_mode", "")
        if mode == "HORIZONTAL":
            return {"type": "horizontal", "direction": "→"}
        if mode == "VERTICAL":
            return {"type": "vertical", "direction": "↓"}
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


# ---- 内边距推断 ----
#
# 当布局容器节点的尺寸明显大于其真实内容子元素所占范围时，差值通常由「内边距(padding)」
# 撑开而非子元素留白。脚本用子元素坐标与容器边界的差值推断上下/左右 padding，仅在差值
# 较小（内边距而非大留白）时输出，帮助开发直接用 padding 而非写死固定 height/width 实现。
# 坐标法比「尺寸相减」更稳：能区分上下/左右 padding 是否相等，并排除与容器等大的背景矩形
# （其不含内容、不占 padding）。不依赖 detect_layout 的方向判定——单内容 + 背景矩形这种
# 场景 detect_layout 易误判，这里直接按垂直/水平两个方向各自的剩余空间分别计算。

PADDING_INFER_MAX = 40  # 单侧内边距推断上限(px)：超过则视为大留白而非内边距


def _is_icon_container(node):
    """判断是否为纯图标容器（≤24px 且内部为图标切图）。

    这类容器的「留白」是图标的视觉内边距，并非布局 padding，不应推断为内边距。
    覆盖 FRAME 与 GROUP 两种类型（build_tree 对 FRAME 图标容器会设 icon_name，
    但 GROUP 图标容器不会，故这里直接按尺寸+子节点类型判定，两处调用保持一致）。
    """
    bn = node.get("bounds", {})
    if bn.get("width", 0) > 24 or bn.get("height", 0) > 24:
        return False
    children = node.get("children", [])
    return any(c.get("component") == "Icon" or c.get("type") in ("VECTOR", "BITMAP")
               for c in children)


def _infer_padding(node):
    """推断容器的上下/左右内边距（不依赖布局方向，分别按两个主轴计算剩余空间）。

    返回 dict：{'vertical': {'top','bottom','valid'}, 'horizontal': {'left','right','valid'}}。
    某方向 valid=True 表示应作为内边距给出（值已在对应 top/bottom/left/right）。

    优先用摹客 autoLayout 权威 padding（且只有 HUG 方向才可能 padding 撑开）；
    autoLayout 缺失时才回退坐标差值法。
    """
    al = get_autolayout(node)
    if al is not None and al["padding"] is not None:
        pad = al["padding"]
        # 仅 HUG（内容撑开）方向才可能是 padding 撑开；FIXED 是定宽定高，不标 padding
        v_valid = al["sizing_v"] == "HUG" and (pad["top"] > 0 or pad["bottom"] > 0)
        h_valid = al["sizing_h"] == "HUG" and (pad["left"] > 0 or pad["right"] > 0)
        return {
            "vertical": {"top": pad["top"], "bottom": pad["bottom"], "valid": v_valid},
            "horizontal": {"left": pad["left"], "right": pad["right"], "valid": h_valid},
        }

    bn = node.get("bounds", {})
    cw, ch = bn.get("width", 0), bn.get("height", 0)
    children = [c for c in node.get("children", []) if c.get("bounds")]

    def empty():
        return {"top": 0, "bottom": 0, "left": 0, "right": 0, "valid": False}

    if len(children) < 1 or cw <= 0 or ch <= 0:
        return {"vertical": empty(), "horizontal": empty()}

    # 背景矩形：尺寸与容器几乎相等（容差 0.5px）且只是填充块，不计入内容边界。
    def is_bg_rect(c):
        cb = c.get("bounds", {})
        return (c.get("type") in ("RECTANGLE", "VECTOR")
                and abs(cb.get("width", 0) - cw) <= 0.5
                and abs(cb.get("height", 0) - ch) <= 0.5)

    content = [c for c in children if not is_bg_rect(c)]
    if not content:
        content = children

    tops = [c.get("bounds", {}).get("top", 0) for c in content]
    bottoms = [c.get("bounds", {}).get("top", 0) + c.get("bounds", {}).get("height", 0) for c in content]
    lefts = [c.get("bounds", {}).get("left", 0) for c in content]
    rights = [c.get("bounds", {}).get("left", 0) + c.get("bounds", {}).get("width", 0) for c in content]

    top = round(min(tops) - bn.get("top", 0))
    bottom = round(bn.get("top", 0) + ch - max(bottoms))
    left = round(min(lefts) - bn.get("left", 0))
    right = round(bn.get("left", 0) + cw - max(rights))

    v_valid = 0 < top <= PADDING_INFER_MAX and 0 < bottom <= PADDING_INFER_MAX
    h_valid = 0 < left <= PADDING_INFER_MAX and 0 < right <= PADDING_INFER_MAX

    return {
        "vertical": {"top": top, "bottom": bottom, "valid": v_valid},
        "horizontal": {"left": left, "right": right, "valid": h_valid},
    }


# ---- 树结构 ----

def build_tree(node, depth=0):
    ntype = get_type(node)
    nname = get_name(node)
    bn = get_bounds(node)
    
    comp_type = infer_component_type(node)

    if ntype == "TEXT":
        ti = get_text_info(node)
        if ti:
            return {"type": ntype, "name": nname, "bounds": bn, "texts": ti, "children": [],
                    "component": comp_type, "fills": get_fill_colors(node), "borders": get_border_count(node)}
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
                "border_info": get_border_info(node),
                "dash": get_dash(node),
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
        # 按阅读顺序（先 top 后 left）排列子文本节点，避免多行文字上下颠倒
        ordered = sorted(
            children,
            key=lambda c: (c.get("bounds", {}).get("top", 0), c.get("bounds", {}).get("left", 0)),
        )
        texts = []
        for c in ordered:
            cb = c.get("bounds", {})
            for t in c.get("texts", []):
                # 保留文字几何位置（用于对齐推断）：摹客常无 alignment 字段，
                # 渲染时据文字中心相对容器中心推断 居中/左/右。
                t = dict(t)
                t["_bounds"] = cb
                texts.append(t)
        tcolors = []
        for c in children:
            for t in c.get("texts", []):
                if t.get("color"):
                    tcolors.append(t["color"])
        # 纯文本容器的子节点已内联为文字行，children 置空；但设计稿若声明了 autoLayout
        # 方向（VERTICAL/HORIZONTAL），应据实输出布局方向，避免纵向排布的多个文本丢失
        # 「vertical ↓」标记。autoLayout 不存在时（layout_mode 缺失）不臆造方向。
        al = get_autolayout(node)
        mode = (al or {}).get("layout_mode", "")
        if mode == "HORIZONTAL":
            layout = {"type": "horizontal", "direction": "→"}
        elif mode == "VERTICAL":
            layout = {"type": "vertical", "direction": "↓"}
        else:
            layout = {"type": "single", "direction": ""}
        return {
            "type": ntype, "name": nname, "bounds": bn,
            "fills": fills, "radius": radius, "borders": borders,
            "border_info": get_border_info(node),
            "dash": get_dash(node),
            "opacity": opacity, "blur": blur,
            "texts": texts, "children": [], "component": comp_type,
            "layout": layout,
            "autolayout": al,
        }

    layout_info = detect_layout(children, node)

    return {
        "type": ntype, "name": nname, "bounds": bn,
        "fills": fills, "radius": radius, "borders": borders,
        "border_info": get_border_info(node),
        "dash": get_dash(node),
        "opacity": opacity, "blur": blur,
        "texts": [], "children": children, "component": comp_type,
        "layout": layout_info,
        "icon_name": icon_name,
        "autolayout": get_autolayout(node),
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
    # 节点名称必须纳入签名：否则不同名的节点（如「1」与「Frame 427321383」）
    # 仅在 type/component/子节点数上相同就会被 merge_repeats 误判为重复而合并掉，
    # 导致页面结构缺失。同名重复组件（如多个「Frame 427321380」）名称相同仍可正常合并。
    parts = [node.get("type", ""), node.get("component", ""), node.get("name", "")]
    for t in node.get("texts", []):
        parts.append(t.get("text", ""))
    for f in node.get("fills", []):
        parts.append(f)
    r = node.get("radius", [])
    if r:
        parts.append(f"r{r}")
    parts.append(f"c{len(node.get('children', []))}")
    return tuple(parts) if len(parts) > 2 else None


# ---- 文档生成 ----

def generate(root, design_width=375):
    tree = build_tree(root)
    if not tree:
        tree = {"type": "FRAME", "name": "Root", "bounds": get_bounds(root),
                "fills": get_fill_colors(root), "texts": [], "children": [], "component": "Container"}
    tree["_root_height"] = get_bounds(root).get("height", 0)

    lines = []
    lines.append("# 设计规格文档\n")
    bn = get_bounds(root)
    fills = get_fill_colors(root)
    bg = fills[0] if fills else "?"
    lines.append(f"**页面**: {bn['width']:.0f}×{bn['height']:.0f}px | **背景**: {bg} | rpx: 1px≈{px2rpx(1)}rpx")
    # 内容区总览：取非全宽的最大子节点宽作为内容区宽度，推算左右 padding
    content_w = 0
    for child in tree.get("children", []):
        cb = child.get("bounds", {})
        cw = cb.get("width", 0)
        if 0 < cw < bn["width"] and cw > content_w:
            content_w = cw
    if content_w and bn["width"]:
        side_pad = round((bn["width"] - content_w) / 2)
        # 单侧留白需同时满足：不超过页宽 15%，且不超过内边距推断上限（PADDING_INFER_MAX），
        # 才视为真实内容 padding；否则判定为画布留白（绝对量概念，用绝对上限兜底，
        # 避免大屏稿阈值被放大到几百 px 仍误判为 padding）。
        if 0 < side_pad <= min(bn["width"] * 0.15, PADDING_INFER_MAX):
            lines.append(f"**内容区**: 宽 {content_w:.0f}px 居中，左右各 {side_pad}px padding（{content_w:.0f}×…px 容器）")
        elif content_w < bn["width"]:
            lines.append(f"**内容区**: 宽 {content_w:.0f}px（页面总宽 {bn['width']:.0f}px，左右含画布留白，非内容 padding）")
    lines.append("")

    # 可复用组件分析（提前计算，用于正文折叠重复实例）
    reusable = _analyze_reusable_components(tree)
    # 建立 path → 可复用组件组号 映射（首实例正常展开，其余折叠为引用）
    reusable_ref = {}
    for i, group in enumerate(reusable, 1):
        for loc in group["locations"][1:]:
            reusable_ref[loc] = i

    lines.append("\n---\n## 页面结构")
    _render(tree, lines, 0, path=tree.get("name", ""), reusable_ref=reusable_ref)

    # 实现要点（跨节点/行为级约束，页面结构树无法体现的全局提示，开发照稿实现必需）
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

    lines.append("\n---\n*由 design-analyzer 自动生成*")
    return "\n".join(lines)


def _render(node, lines, depth=0, path="", reusable_ref=None, prefix="", is_last=True):
    name = node["name"]
    bn = node["bounds"]
    w, ht = bn.get("width", 0), bn.get("height", 0)
    comp_type = node.get("component", "")

    flags = []
    # TEXT 节点的 fills 是字体颜色（已在文字行以 #色值 呈现），不是背景，勿输出 bg=
    is_text = node.get("type") == "TEXT"
    fills = node.get("fills", [])
    if fills and not is_text:
        flags.append(f"bg={','.join(fills)}")
    radius = node.get("radius", [])
    if radius:
        flags.append(f"r={radius}")
    borders = node.get("border_info", [])
    if borders:
        flags.append(f"border={' / '.join(borders)}")
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

    # 宽度意图旗标：优先用摹客 autoLayout 权威判断（HUG=内容撑开），无 autoLayout 回退坐标法。
    width_flag = ""
    if w > 0 and node.get("children"):
        al = get_autolayout(node)
        if al is not None:
            if al["sizing_h"] == "HUG":
                width_flag = "内容撑开"
        else:
            hpad = _infer_padding(node).get("horizontal", {})
            if hpad.get("valid"):
                width_flag = "内容撑开"

    # 树形连接符：根节点无前缀；其余用 ├── / └──
    if depth == 0:
        connector = ""
    else:
        connector = "└── " if is_last else "├── "

    wflag_str = f" [{width_flag}]" if width_flag else ""
    lines.append(f"{prefix}{connector}{name} {w:.0f}×{ht:.0f}px{wflag_str}{flag_str}")

    # 子节点/注解的缩进前缀：当前层级往下传 ┊/空格
    child_prefix = prefix
    if depth > 0:
        child_prefix += "    " if is_last else "│   "
    anno_prefix = child_prefix + "  "

    # 输出图标名称（如果有）
    icon_name = node.get("icon_name", "")
    if icon_name:
        lines.append(f"{anno_prefix}- [图标: {icon_name}]")

    for t in node.get("texts", []):
        fs = f"fs={t['fontSize']}" if t.get("fontSize") else ""
        fw = f"fw={t['fontWeight']}" if t.get("fontWeight") else ""
        fc = t.get("color", "")
        lh = f"lh={t['lineHeight']}" if t.get("lineHeight") else ""
        alignment = t.get("alignment", "")
        if not alignment:
            # 摹客常无 alignment 字段：据文字中心相对容器中心的水平位置推断对齐，
            # 避免表头/标题类节点对齐缺失导致开发瞎猜。
            tb = t.get("_bounds") or {}
            if tb and w > 0:
                t_left = tb.get("left", 0)
                t_w = tb.get("width", 0)
                t_right = t_left + t_w
                p_left = bn.get("left", 0)
                p_right = p_left + w
                left_margin = t_left - p_left
                right_margin = p_right - t_right
                if abs(left_margin - right_margin) <= max(4, w * 0.12):
                    alignment = "居中"
                elif left_margin <= right_margin:
                    alignment = "左"
                else:
                    alignment = "右"
        align_str = f"align={alignment}" if alignment else ""
        extra = " ".join(filter(None, [fs, fw, lh, align_str]))
        lines.append(f"{anno_prefix}- `{t['text']}`  {extra}  {fc}")

    raw_children = [c for c in node.get("children", [])]

    # 过滤纯空节点：摹客共享样式/变体组件的子节点有时未随实例还原（build_tree 后
    # children 为空），这类节点无子、无文字、无样式，对实现零价值，渲染成空格子反而误导开发。
    def _is_empty_node(c):
        return (
            not c.get("children") and not c.get("texts")
            and not c.get("fills") and not c.get("borders")
            and not c.get("radius") and c.get("opacity", 1) >= 1
            and not c.get("blur") and not c.get("icon_name")
        )
    raw_children = [c for c in raw_children if not _is_empty_node(c)]
    # 排序规则（按布局方向），间距计算与渲染共用，保证顺序一致
    layout = node.get("layout", {})

    def sort_children(seq):
        if layout.get("type") == "horizontal":
            if seq and all(c.get("type") == "TEXT" for c in seq):
                return sorted(seq, key=lambda c: (c.get("bounds", {}).get("top", 0), c.get("bounds", {}).get("left", 0)))
            return sorted(seq, key=lambda c: c.get("bounds", {}).get("left", 0))
        elif layout.get("type") == "grid":
            return sorted(seq, key=lambda c: (c.get("bounds", {}).get("top", 0), c.get("bounds", {}).get("left", 0)))
        return sorted(seq, key=lambda c: c.get("bounds", {}).get("top", 0))

    # 间距基于「未合并的原始 children」计算：保留重复实例（×N 折叠会丢失组内间距），
    # 避免把一组重复组件的总跨度误算成单段间距。
    ordered_raw = sort_children(raw_children)
    if len(ordered_raw) > 1:
        gaps = []
        # 按布局方向选间距公式：水平布局取左右间隙，其余取上下间隙
        is_h = layout.get("type") == "horizontal"
        for i in range(len(ordered_raw) - 1):
            a_bn = ordered_raw[i].get("bounds", {})
            b_bn = ordered_raw[i + 1].get("bounds", {})
            if is_h:
                g = round(b_bn.get("left", 0) - (a_bn.get("left", 0) + a_bn.get("width", 0)), 1)
            else:
                g = round(b_bn.get("top", 0) - (a_bn.get("top", 0) + a_bn.get("height", 0)), 1)
            if g > 0:
                a_name = ordered_raw[i].get("name", "")
                b_name = ordered_raw[i + 1].get("name", "")
                gaps.append((a_name, b_name, g))
        if gaps:
            # 间距列表与子节点树同级缩进（用 child_prefix），保持树形视觉连续。
            # 页面根节点下的区块间距（卡片间距/section 间距）对实现同样关键，不再跳过。
            lines.append(f"{child_prefix}- 子元素间距:")
            for a, b_name, g in gaps[:10]:
                lines.append(f"{child_prefix}  - {a} → {b_name}: {g}px ({px2rpx(g)}rpx)")

    # 纯文本容器（children 已内联为文字行）：子元素间距来自 autoLayout 的 itemSpacing，
    # 因 raw_children 为空、坐标法失效，改用 autoLayout 权威值直接输出，避免纵向排布的
    # 多个文本丢失间距信息。autoLayout 缺失或间距为 0 时不输出，避免臆造。
    if not raw_children and node.get("texts"):
        al = get_autolayout(node)
        isp = (al or {}).get("item_spacing", 0) or 0
        if isp > 0:
            lines.append(f"{child_prefix}- 子元素间距:")
            lines.append(f"{child_prefix}  - 文字行间距: {isp}px ({px2rpx(isp)}rpx)")

    # 内边距推断：仅当某方向差值较小（内边距而非大留白）时注解。
    # 纯图标容器的"留白"是图标视觉内边距，非布局 padding，跳过。
    pad = {"vertical": {"top": 0, "bottom": 0, "valid": False},
           "horizontal": {"left": 0, "right": 0, "valid": False}} if _is_icon_container(node) else _infer_padding(node)
    if pad["vertical"]["valid"]:
        vt, vb = pad["vertical"]["top"], pad["vertical"]["bottom"]
        if vt == vb:
            lines.append(f"{anno_prefix}- [内边距] 上下各 {vt}px（容器高度由子元素高度 + 上下 padding 撑开）")
        else:
            lines.append(f"{anno_prefix}- [内边距] 上 {vt}px / 下 {vb}px（上下 padding 不等，容器高度由子元素 + padding 撑开）")
    if pad["horizontal"]["valid"]:
        vl, vr = pad["horizontal"]["left"], pad["horizontal"]["right"]
        if vl == vr:
            lines.append(f"{anno_prefix}- [内边距] 左右各 {vl}px（容器宽度由子元素宽度 + 左右 padding 撑开）")
        else:
            lines.append(f"{anno_prefix}- [内边距] 左 {vl}px / 右 {vr}px")

    children = merge_repeats(raw_children)
    sorted_children = sort_children(children)

    for idx, child in enumerate(sorted_children):
        child_path = f"{path} > {child.get('name', '')}" if path else child.get("name", "")
        if reusable_ref and child_path in reusable_ref:
            connector = "└── " if idx == len(sorted_children) - 1 else "├── "
            lines.append(f"{child_prefix}{connector}[复用] {child.get('name', '')} — 结构同上（与上方同类实例一致，已首次完整展开）")
            continue
        _render(child, lines, depth + 1, path=child_path, reusable_ref=reusable_ref,
                prefix=child_prefix, is_last=(idx == len(sorted_children) - 1))


def _generate_impl_notes(tree):
    """生成实现要点，帮助开发者快速准确地实现设计稿。"""
    notes = []
    # 收集页面中所有信息字段行（Form Row / Text Block）是否带分隔线，
    # 供 analyze 结束后合并成一条精确的「信息字段行」要点，避免逐行输出造成歧义。
    field_rows = []

    def analyze(node, depth=0, siblings_after=None):
        comp_type = node.get("component", "")
        nname = node.get("name", "")
        fills = node.get("fills", [])
        borders = node.get("borders", 0)
        children = node.get("children", [])
        bn = node.get("bounds", {})

        # 检测 info-row 类型（Form Row 或 Text Block 的字段行）
        # 排除表格单元格：自身或子节点带 border 的格子是表格列，不是信息字段行
        _is_table_cell = borders > 0 or any(c.get("borders", 0) for c in children)
        if comp_type in ("Form Row", "Text Block") and len(children) >= 2 and not _is_table_cell:
            # 基于真实数据判断字段行之间是否有分隔线：仅认 component==Divider
            # （由 infer_component_type 按 height<=1 判定的真实分隔线，如 Vector 分隔线）。
            # 不能把 RECTANGLE 类型都算分隔线——Tab 高亮指示条(如蓝色小横条)虽是 RECTANGLE 但并非分隔线。
            has_divider = any(c.get("component") == "Divider" for c in children)
            field_rows.append((nname, has_divider))

        # 检测白色背景的大区块（可能是信息区或商品区）
        if comp_type == "Container" and fills and "#FFFFFF" in fills:
            w = bn.get("width", 0)
            h = bn.get("height", 0)
            root_w = tree.get("bounds", {}).get("width", 0) or 0
            if root_w and w >= root_w * 0.85 and h >= 200:
                page_bg = tree.get("fills", ["#F2F2F2"])[0] if tree.get("fills") else "#F2F2F2"
                notes.append(f"**白色区块**（`{nname or 'Container'}`）：背景色为 `#FFFFFF`，与页面背景 `{page_bg}` 区分")
        
        # 检测 Vector 分隔线
        for child in children:
            if child.get("component") == "Divider" and child.get("type") == "VECTOR":
                c_bn = child.get("bounds", {})
                if c_bn.get("height", 0) <= 1:
                    notes.append(f"**分隔线**：`{child.get('name', 'Vector')}` 是 1px 高度的分隔线，注意与 1px 间距区分")
        
        for idx, child in enumerate(children):
            rest = children[idx + 1:]
            analyze(child, depth + 1, siblings_after=rest)
    
    analyze(tree)

    # 统一输出「信息字段行」要点（合并所有字段行的检测结果，避免逐行输出造成歧义）
    if field_rows:
        # 去重：同名节点可能多次进入 field_rows（如跨两个表格实例）
        with_div = list(dict.fromkeys(name for name, d in field_rows if d))
        if with_div:
            names = "、".join(f"「{n}」" for n in with_div)
            notes.append(
                f"**信息字段行**：字段行之间默认**没有分隔线**（不要用 border-bottom）；"
                f"仅 {names} 带分隔线/指示条，按需实现"
            )
        else:
            notes.append(f"**信息字段行**：字段行之间**没有分隔线**，不要用 border-bottom")

    # 检测底部菜单栏：基于结构（位于页面底部 + 含多个按钮形态子节点）识别，
    # 不依赖类型标记（摹客原始导出无 Navigation 类型）。判断不了就不输出，避免误导。
    root_h = tree.get("bounds", {}).get("height", 0) or 0
    for child in tree.get("children", []):
        cname = child.get("name", "")
        cb = child.get("bounds", {})
        at_bottom = root_h and (cb.get("top", 0) + cb.get("height", 0)) >= root_h - 5
        # 含多个带文字的按钮形态子节点（按钮/普通文字项 >=2 个）
        btn_like = [c for c in child.get("children", []) if c.get("texts") or c.get("component") == "Button"]
        if ("菜单栏" in cname) or (at_bottom and len(btn_like) >= 2):
            notes.append(
                f"**底部菜单栏**：建议**固定吸底**（`position: fixed/sticky`，bottom: 0），"
                f"层级高于内容区，白色背景遮挡下方滚动内容"
            )
            break

    # 全局默认原则（始终输出，作为实现要点首条）：容器尺寸优先用 padding 撑开，不写死宽高。
    default_principle = (
        "**默认原则：容器尺寸优先用 `padding` 撑开，不要写死固定 height/width**。"
        "文档「页面结构」树中标注 `[内边距]` 的容器，其尺寸由子元素 + 上下/左右 padding 撑开，"
        "实现时用 `padding` 即可（精确值见树内 [内边距] 标注）。仅两种例外才写死："
        "① 脚本对该容器尺寸/内容识别不稳定、实现时可能崩溃或报错；② 用户明确要求固定值。"
    )
    notes.insert(0, default_principle)

    # 添加通用要点
    if not notes:
        notes.append("请严格按照规格文档中的色值、字号、间距实现")
    
    return notes


def _collect_radii(node):
    """收集节点子树内所有圆角值（含自身与后代），返回排序后的稳定字符串标识。"""
    rs = []

    def walk(n):
        r = n.get("radius", [])
        if r:
            rs.append(tuple(r))
        for c in n.get("children", []):
            walk(c)

    walk(node)
    return str(sorted(rs))


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
            def _child_sig(c, depth=0):
                c_type = c.get("component", c.get("type", ""))
                c_texts = [t.get("text", "") for t in c.get("texts", [])]
                # 无文字但含子节点时，递归穿透到真实 TEXT 叶子取文字，避免签名退化成
                # 纯类型串（如 Form Row/List Item 包文字却无自身 texts，会被误判成
                # 同构而折叠——表格表头行就因此被吞掉）。仅对容器型节点递归，TEXT 等
                # 叶子节点无 children，自然落到下面的文字分支。
                container_types = ("Container", "Card", "Form Row", "List Item",
                                   "Text Block", "List Container", "Navigation")
                if not c_texts and c_type in container_types and c.get("children"):
                    if depth < 4:
                        sub = [_child_sig(sc, depth + 1) for sc in c.get("children", [])]
                        if sub:
                            return f"{c_type}(布局): " + " / ".join(sub)
                    return f"{c_type}(布局)"
                return f"{c_type}:{'|'.join(c_texts[:2])}"

            child_sigs = [_child_sig(c) for c in children]
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
            children_desc = rep["children_desc"]
            # 检测组内成员深层圆角是否一致：若不一致，提示开发以正文各实例为准，
            # 避免合并描述误导（如两个 Group 实例内部的 Rounded Box 圆角不同）
            radii = set()
            for it in items:
                r = _collect_radii(it["node"])
                radii.add(r)
            if len(radii) > 1:
                children_desc += "（注：各实例内部圆角可能不同，以正文对应实例为准）"
            reusable.append({
                "name": rep["name"],
                "component": rep["component"],
                "size": rep["size"],
                "bg": rep["bg"],
                "radius": rep["radius"],
                "border": rep["border"],
                "children_desc": children_desc,
                "count": len(items),
                "locations": [item["path"] for item in items],
            })
    
    # 按出现次数降序
    reusable.sort(key=lambda x: x["count"], reverse=True)
    return reusable


def main():
    p = argparse.ArgumentParser(description="生成设计规格文档")
    p.add_argument("file", help="摹客 JSON 文件路径")
    p.add_argument("-o", "--output", help="输出文件路径（默认输出到运行命令所在目录下的 design-analyzer/spec_页面名.md）")
    p.add_argument("-w", "--width", type=int, default=375, help="设计图宽度（默认 375px）")
    args = p.parse_args()

    with open(args.file) as f:
        flat_nodes = json.load(f)

    # 摹客 JSON 是扁平列表，需要转换为嵌套树
    root = build_tree_from_flat_list(flat_nodes)

    page_name = root.get("basic", {}).get("name", "design")
    if args.output:
        out_file = args.output
    else:
        # 默认输出到「用户当前工作目录」下的 design-analyzer/ 子目录，自动创建。
        # 注意：这里是用户运行命令时所在的目录（即项目目录），而非脚本/skill 所在目录。
        out_dir = os.path.join(os.getcwd(), "design-analyzer")
        os.makedirs(out_dir, exist_ok=True)
        out_file = os.path.join(out_dir, f"spec_{page_name}.md")

    md = generate(root, args.width)

    with open(out_file, "w") as f:
        f.write(md)
    print(f"✅ 规格文档已生成: {out_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
