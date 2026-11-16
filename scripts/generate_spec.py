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


def _parse_color(s):
    """把 `#RRGGBB` / `#RRGGBBAA` / `rgba(r,g,b,a)` / `rgb(r,g,b)` 解析成 (r,g,b,a)。"""
    s = (s or "").strip()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 8:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16) / 255)
        if len(h) == 6:
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
        return (0, 0, 0, 1)
    if s.startswith("rgba"):
        nums = s[s.find("(") + 1:s.find(")")].split(",")
        return (int(nums[0]), int(nums[1]), int(nums[2]), float(nums[3]) if len(nums) > 3 else 1)
    if s.startswith("rgb"):
        nums = s[s.find("(") + 1:s.find(")")].split(",")
        return (int(nums[0]), int(nums[1]), int(nums[2]), 1)
    return (0, 0, 0, 1)


def _composite_layers(layers):
    """把多层填充（底→顶）alpha 合成为最终 hex。

    摹客常把「实底色 + 白色半透明叠加」作为样式导出（如 #F03C3C + 白0.9）。
    开发直接 Copy 任一层都会用错（本次背景过深即源于此）。这里机械合成出
    最终可见色并返回，文档只输出这一个值，不列出原始分层。单层原样返回。
    """
    if not layers:
        return ""
    if len(layers) == 1:
        return layers[0]
    cur = _parse_color(layers[0])
    for nxt in layers[1:]:
        fg = _parse_color(nxt)
        a = fg[3]
        if a >= 1:
            cur = fg
        else:
            cur = (
                fg[0] * a + cur[0] * (1 - a),
                fg[1] * a + cur[1] * (1 - a),
                fg[2] * a + cur[2] * (1 - a),
                1,
            )
    return "#%02X%02X%02X" % (round(cur[0]), round(cur[1]), round(cur[2]))


def get_color(node, kind="fill"):
    """统一获取节点的「最终可见颜色」——脚本中所有取色都应走这里。

    kind='fill'：取填充色并底→顶 alpha 合成为最终 hex。兼容两种节点形态：
      原始 JSON 节点（node.fill.colors）与已构建的树节点（node.fills 为颜色字符串列表）。
      多层叠加时自动合成（如红底 + 白0.9），单图层原样返回；无填充返回 ''。
      背景色与文字色都走此路径。
    kind='border'：取描边颜色（边框通常为单色，不做合成），返回「Wpx #COLOR」列表；无边框返回 []。
      兼容树节点（node.border_info）与原始节点。
    """
    if kind == "fill":
        colors = node.get("fills")
        if not colors:
            raw = node.get("fill")
            if raw and raw.get("colors"):
                colors = get_fill_colors(node)
        return _composite_layers(colors or [])
    if kind == "border":
        bi = node.get("border_info")
        if bi is not None:
            return bi
        return get_border_info(node)
    return ""


def px2rpx(px, design_width=375):
    return round(px * 750 / design_width)


def _fmt_px(v):
    """数值格式化：整数去 .0，小数保留 1 位（如 25.5），不抹掉半像素。"""
    v = round(float(v), 1)
    return str(int(v)) if v == int(v) else str(v)


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
    padding（{left,top,right,bottom}，缺失键按 0）、item_spacing（子元素间距）、
    primary_align / counter_align（主轴/交叉轴对齐，CENTER/MIN/MAX 等）。
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
        "primary_align": al.get("primaryAxisAlignItems", ""),
        "counter_align": al.get("counterAxisAlignItems", ""),
    }


def get_blur(node):
    return node.get("blur", {}).get("radius", 0)


def get_slice_image(node):
    """返回图片切图信息：摹客导出的 INSTANCE/symbol/BITMAP 组件带 slice 字段，
    含 bitmapURL（位图）与 svgURL（矢量切图）。无图片则返回 None，否则返回
    dict：{'bitmap': url, 'svg': url}（缺哪项则对应值为空字符串）。"""
    sl = node.get("slice") or {}
    if not isinstance(sl, dict):
        return None
    bmp = sl.get("bitmapURL", "") or ""
    svg = sl.get("svgURL", "") or ""
    if not bmp and not svg:
        return None
    return {"bitmap": bmp, "svg": svg}


def _is_image_component(node):
    """判断节点是否为「图片/图标组件」：INSTANCE/symbol/BITMAP 类型且带切图 URL。

    与 infer_component_type、build_tree 共用同一判定来源（get_slice_image），
    避免两处口径分歧导致 component 类型与渲染出的切图链接不一致。
    """
    return get_slice_image(node) is not None



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

    if ntype in ("INSTANCE", "BITMAP", "symbol"):
        # 摹客导出的图片/图标组件（如空状态插图 undraw_no_data）以 INSTANCE(或
        # symbol/BITMAP) + slice.bitmapURL 形式出现，带切图 URL 即视为图片图标。
        if _is_image_component(node):
            return "Image/Icon"
        return "Component"

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
    # 子节点为图标（小尺寸 Vector/Bitmap 推断的 Icon，或 INSTANCE/symbol 带切图的
    # Image/Icon 图片组件）即视为图标容器，避免把含图片切图子节点的容器误判成
    # 需推断内边距的布局容器（其「留白」是图标视觉内边距，非布局 padding）。
    return any(c.get("component") in ("Icon", "Image/Icon")
               or c.get("type") in ("VECTOR", "BITMAP")
               for c in children)


def _measured_content_inset(node):
    """实测「内容包围盒到容器边缘」的距离（坐标法，含内联文字行）。

    返回 {'top','bottom','left','right'} 或 None（无内容/无尺寸）。与 padding
    字段值的区别：容器内容居中（FIXED/FILL 常见）时，字段值被居中布局吸收、
    不等于内容到边缘的真实距离（如按钮 padding 字段 10、实测左右 60/上下 14）；
    而实测距离才是「padding + 内容撑出尺寸」这一响应式实现所需的正确值。
    """
    bn = node.get("bounds", {})
    cw, ch = bn.get("width", 0), bn.get("height", 0)
    children = [c for c in node.get("children", []) if c.get("bounds")]
    if cw <= 0 or ch <= 0:
        return None
    if len(children) < 1 and not node.get("texts"):
        return None

    ntop = bn.get("top", 0)
    nleft = bn.get("left", 0)

    # 背景矩形/段：尺寸与容器几乎相等且只是填充块，不计入内容边界（逻辑同
    # _infer_padding 的坐标法分支，两处口径保持一致）。
    def is_bg_rect(c):
        cb = c.get("bounds", {})
        w, h = cb.get("width", 0), cb.get("height", 0)
        rel_t = round(cb.get("top", 0) - ntop)
        rel_l = round(cb.get("left", 0) - nleft)
        if c.get("type") not in ("RECTANGLE", "VECTOR"):
            return False
        full_h = abs(h - ch) <= 0.5
        full_w = abs(w - cw) <= 0.5
        flush_t = abs(rel_t) <= 0.5
        flush_l = abs(rel_l) <= 0.5
        return (full_h and full_w) or (full_h and flush_t) or (full_w and flush_l)

    rects = []
    for c in children:
        if not is_bg_rect(c):
            rects.append(c.get("bounds"))
    for t in node.get("texts", []):
        tb = t.get("_bounds")
        if tb:
            rects.append(tb)
    if not rects:
        rects = [c.get("bounds") for c in children] or None
    if not rects:
        return None

    tops = [b.get("top", 0) for b in rects]
    bottoms = [b.get("top", 0) + b.get("height", 0) for b in rects]
    lefts = [b.get("left", 0) for b in rects]
    rights = [b.get("left", 0) + b.get("width", 0) for b in rects]

    return {
        "top": round(min(tops) - ntop),
        "bottom": round(ntop + ch - max(bottoms)),
        "left": round(min(lefts) - nleft),
        "right": round(nleft + cw - max(rights)),
    }


def _infer_padding(node):
    """推断容器的上下/左右内边距（不依赖布局方向，分别按两个主轴计算剩余空间）。

    返回 dict：{'vertical': {'top','bottom','valid'}, 'horizontal': {'left','right','valid'}}。
    某方向 valid=True 表示应作为内边距给出（值已在对应 top/bottom/left/right）。

    优先用摹客 autoLayout 权威 padding；autoLayout 缺失时回退坐标差值法
    （复用 _measured_content_inset，另受 PADDING_INFER_MAX 上限约束——超过
    视为大留白而非内边距）。注意：FIXED/FILL 且内容居中的容器，字段值会被
    居中布局吸收、不等于内容到边缘的真实距离，_render 对这类方向改用
    _measured_content_inset 的实测值。
    """
    al = get_autolayout(node)
    if al is not None and al["padding"] is not None:
        pad = al["padding"]
        # 任意方向，只要 autolayout 明确给了 padding 就输出（不再仅限 HUG）：
        # FIXED 定尺寸容器同样可能内边距撑开，开发照值实现即可。
        v_valid = pad["top"] > 0 or pad["bottom"] > 0
        h_valid = pad["left"] > 0 or pad["right"] > 0
        return {
            "vertical": {"top": pad["top"], "bottom": pad["bottom"], "valid": v_valid},
            "horizontal": {"left": pad["left"], "right": pad["right"], "valid": h_valid},
        }

    def empty():
        return {"top": 0, "bottom": 0, "left": 0, "right": 0, "valid": False}

    mi = _measured_content_inset(node)
    if mi is None:
        return {"vertical": empty(), "horizontal": empty()}

    v_valid = 0 < mi["top"] <= PADDING_INFER_MAX and 0 < mi["bottom"] <= PADDING_INFER_MAX
    h_valid = 0 < mi["left"] <= PADDING_INFER_MAX and 0 < mi["right"] <= PADDING_INFER_MAX

    return {
        "vertical": {"top": mi["top"], "bottom": mi["bottom"], "valid": v_valid},
        "horizontal": {"left": mi["left"], "right": mi["right"], "valid": h_valid},
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
            raw_fills = get_fill_colors(node)
            comp = get_color(node, "fill")
            if comp:
                for t in ti:
                    t["color"] = comp
            return {"type": ntype, "name": nname, "bounds": bn, "texts": ti, "children": [],
                    "component": comp_type, "fills": raw_fills, "borders": get_border_count(node)}
        return None

    if ntype not in ("FRAME", "GROUP"):
        fills = get_fill_colors(node)
        radius = get_radius(node)
        borders = get_border_count(node)
        slice_img = get_slice_image(node)
        if fills or radius or borders or slice_img:
            # 提取图标名称（从节点名中推断）
            icon_name = ""
            if ntype in ("VECTOR", "BITMAP"):
                bw = bn.get("width", 0)
                bh = bn.get("height", 0)
                # 小尺寸单色 Vector/Bitmap 视为图标
                if bw <= 44 and bh <= 44 and len(fills) <= 1 and borders <= 1:
                    # 保留原始名称（如 "Cap", "路径 2" 等），方便对应切图文件
                    icon_name = nname
            elif slice_img:
                # 带切图 URL 的 INSTANCE/symbol/BITMAP 组件（如空状态插图），
                # 无论尺寸都把名称保留为图标名，方便对照切图文件实现占位图。
                icon_name = nname

            return {
                "type": ntype, "name": nname, "bounds": bn,
                "fills": fills, "radius": radius, "borders": borders,
                "border_info": get_border_info(node),
                "dash": get_dash(node),
                "texts": [], "children": [], "component": comp_type,
                "icon_name": icon_name,
                "slice_img": slice_img,
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
    # 切图 URL 纳入签名：多个同名、同尺寸、同类型的空状态插图（如不同页的
    # undraw 占位图）若仅按名称/尺寸判定会被 merge_repeats 误合并成 ×N 而丢失其一；
    # 切图 URL 不同则签名不同、各自保留，同一切图的真正重复实例仍可正常合并。
    sl = node.get("slice_img") or {}
    if sl.get("bitmap") or sl.get("svg"):
        parts.append(f"img:{sl.get('bitmap') or sl.get('svg')}")
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
    bg = get_color(root, "fill") or "?"
    lines.append(f"**页面**: {bn['width']:.0f}×{bn['height']:.0f}px | **背景**: {bg} | 单位: px（rpx 仅供参考：1px≈{px2rpx(1)}rpx）")
    # 内容区总览：取非全宽的最大子节点宽作为内容区宽度，推算左右 padding
    content_w = 0
    content_h = 0
    for child in tree.get("children", []):
        cb = child.get("bounds", {})
        cw = cb.get("width", 0)
        if 0 < cw < bn["width"] and cw > content_w:
            content_w = cw
            content_h = cb.get("height", 0)
    if content_w and bn["width"]:
        side_pad = round((bn["width"] - content_w) / 2)
        # 单侧留白需同时满足：不超过页宽 15%，且不超过内边距推断上限（PADDING_INFER_MAX），
        # 才视为真实内容 padding；否则判定为画布留白（绝对量概念，用绝对上限兜底，
        # 避免大屏稿阈值被放大到几百 px 仍误判为 padding）。
        if 0 < side_pad <= min(bn["width"] * 0.15, PADDING_INFER_MAX):
            content_size = f"{content_w:.0f}×{content_h:.0f}px" if content_h else f"{content_w:.0f}×…px"
            lines.append(f"**内容区**: 宽 {content_w:.0f}px 居中，左右各约 {side_pad}px padding（{content_size} 容器）")
        elif content_w < bn["width"]:
            lines.append(f"**内容区**: 宽 {content_w:.0f}px（页面总宽 {bn['width']:.0f}px，左右含画布留白，非内容 padding）")
    lines.append("")

    # 可复用组件分析（提前计算，用于正文折叠重复实例）
    reusable = _analyze_reusable_components(tree)
    # 建立 path → 复用代表描述 映射（首实例正常展开，其余折叠为引用）
    reusable_ref = {}
    for group in reusable:
        # 值存「代表实例名 + 尺寸」：复用按结构签名分组，代表可能与被折叠实例
        # 不同名（如 427321776 复用 427321775 的结构）；而同名实例也可能对应
        # 不同结构（如 Frame 427321777 同时有 245×46 与 245×20 两种），仅给
        # 名字仍会指代落空。带尺寸后实现者可精确定位到唯一的完整展开行。
        # membership 判断不受影响。
        ref = f"{group['name']} {group['size']}"
        for loc in group["locations"][1:]:
            reusable_ref[loc] = ref

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


def _render(node, lines, depth=0, path="", reusable_ref=None, prefix="", is_last=True,
            center_flag="", parent_bounds=None):
    name = node["name"]
    bn = node["bounds"]
    w, ht = bn.get("width", 0), bn.get("height", 0)
    comp_type = node.get("component", "")

    flags = []
    # TEXT 节点的 fills 是字体颜色（已在文字行以 #色值 呈现），不是背景，勿输出 bg=
    is_text = node.get("type") == "TEXT"
    if not is_text:
        bg = get_color(node, "fill")
        if bg:
            flags.append(f"bg={bg}")
    radius = node.get("radius", [])
    if radius:
        flags.append(f"r={radius}")
    borders = get_color(node, "border")
    if borders:
        flags.append(f"border={' / '.join(borders)}")
    opacity = node.get("opacity", 1)
    hidden_flag = ""
    if opacity <= 0.01:
        # 设计稿隐藏图层：裸 opacity=0.0 不够醒目，开发容易照着画出来（如列表
        # 次行残留的 radio），改为显式「不可见」旗标。必须强调保留占位——
        # opacity=0 节点仍占布局空间（兄弟间距/文字位置靠它撑出），删节点会
        # 让相邻元素左移/上移，与设计稿不符。
        hidden_flag = "不可见: opacity=0，不渲染但保留占位"
    elif opacity < 1:
        flags.append(f"opacity={opacity}")
    blur = node.get("blur", 0)
    if blur:
        flags.append(f"blur={blur}")
    repeat = node.get("repeat", 0)
    if repeat:
        flags.append(f"×{repeat}")
    layout = node.get("layout", {})
    if _overlay_children(node):
        # 该节点的直接子节点是「全屏半透明遮罩 + 浮于其上的内容容器」叠层结构，
        # 不是垂直/水平排列。标 vertical 会与实现要点的叠层说明自相矛盾。
        flags.append("叠层 ↕")
    elif layout.get("type") and layout.get("type") != "single":
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
    cflag_str = f" [{center_flag}]" if center_flag else ""
    hflag_str = f" [{hidden_flag}]" if hidden_flag else ""
    lines.append(f"{prefix}{connector}{name} {w:.0f}×{ht:.0f}px{wflag_str}{cflag_str}{hflag_str}{flag_str}")

    # 子节点/注解的缩进前缀：当前层级往下传 ┊/空格
    child_prefix = prefix
    if depth > 0:
        child_prefix += "    " if is_last else "│   "
    anno_prefix = child_prefix + "  "

    # 输出图标名称（如果有）
    icon_name = node.get("icon_name", "")
    if icon_name:
        slice_img = node.get("slice_img")
        if slice_img:
            url = slice_img.get("bitmap") or slice_img.get("svg")
            lines.append(f"{anno_prefix}- [图标: {icon_name}] 图片切图: {url}")
        else:
            lines.append(f"{anno_prefix}- [图标: {icon_name}]")

    for t in node.get("texts", []):
        fs = f"fs={t['fontSize']}" if t.get("fontSize") else ""
        fw = f"fw={t['fontWeight']}" if t.get("fontWeight") else ""
        fc = t.get("color", "")
        lh = f"lh={t['lineHeight']}" if t.get("lineHeight") else ""
        style_align = t.get("alignment", "")
        # 文字盒对齐据「文字盒相对基准容器的几何位置」推导，不直接采信文字
        # 样式里的 align 字段作结论：文字盒在容器内居中（左右 inset 近似相等）时，
        # 样式 align=LEFT 并不代表文字盒视觉上左对齐（标题类节点还原事故的根因）。
        # 注意这只描述「文字盒」的对齐，不代表盒内文字本身居中——盒内文字仍按
        # 内部 align 排布，故输出用「文字盒对齐」而非笼统的「视觉对齐」。
        # 基准容器：内联文字用所在容器；独立 TEXT 子节点用父容器（文字盒必然
        # 撑满自身，对自身推断无意义）。
        tb = t.get("_bounds") or {}
        base_bn = parent_bounds if (is_text and parent_bounds) else bn
        base_w = base_bn.get("width", 0) or w
        visual_align = ""
        geom = None  # (框宽, 基准容器宽, 左inset, 右inset)，冲突时展示供复核
        if tb and base_w > 0:
            t_left = tb.get("left", 0)
            t_w = tb.get("width", 0)
            t_right = t_left + t_w
            p_left = base_bn.get("left", 0)
            p_right = p_left + base_w
            lm = t_left - p_left
            rm = p_right - t_right
            if lm <= 1 and rm <= 1:
                # 文字盒撑满基准容器：几何推断无意义，文字盒对齐即内部 align
                # （无样式时回退居中，与历史行为一致）
                visual_align = style_align or "居中"
            elif abs(lm - rm) <= max(4, 0.25 * min(lm, rm)):
                # 居中判据：两侧 inset 近似相等（容差取较小 inset 的 25%，下限 4px）。
                # 不按容器宽取比例——宽容器下 24/0 这类明显左贴边会被误判居中。
                visual_align = "居中"
            elif lm <= rm:
                visual_align = "左"
            else:
                visual_align = "右"
            geom = (t_w, base_w, lm, rm)
        if style_align and visual_align and style_align != visual_align and visual_align == "居中":
            # 样式对齐与文字盒对齐冲突且几何推导为居中：两者分列，附几何数据供
            # 消费方复核，实现以 文字盒对齐 为准（见 SKILL.md 文字行格式说明）。
            # 仅居中冲突才展示——几何为 左/右 的差异多来自 flex 行内盒子贴边
            # （如 radio+文字行），盒子位置是布局结果而非对齐意图，静默以样式为准。
            # 几何复核数据用无歧义命名：避免「框」被读成「边框」；等号表明是
            # 属性值，「距父X缘」表明是文字盒到父容器边缘的距离。
            # 「本行文字节点宽=geom[0]」就是紧邻节点行标的那個宽×高，强调它
            # 不是另一层框（实现者曾据此误读成两层）；「所在父容器宽」明确
            # 参照的是 layout 父容器，满足 距左+本节点宽+距右=父容器宽。
            g = (f" [本行文字节点宽={_fmt_px(geom[0])} / 所在父容器宽={_fmt_px(geom[1])}"
                 f" / 距父左缘={_fmt_px(geom[2])} / 距父右缘={_fmt_px(geom[3])}]") if geom else ""
            align_str = f"内部align={style_align} 文字盒对齐={visual_align}{g}"
        else:
            # 无冲突或非居中差异：有样式字段时以样式为准，否则输出几何推断结论
            align_str = f"align={style_align or visual_align}" if (style_align or visual_align) else ""
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
            and not c.get("slice_img")
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
    # 仅 ≥2 行文字才输出「文字行间距」——单文字容器（如按钮）的 itemSpacing
    # 无对象，输出成噪声曾被实现者追问。
    if not raw_children and len(node.get("texts", [])) > 1:
        al = get_autolayout(node)
        isp = (al or {}).get("item_spacing", 0) or 0
        if isp > 0:
            lines.append(f"{child_prefix}- 子元素间距:")
            lines.append(f"{child_prefix}  - 文字行间距: {isp}px ({px2rpx(isp)}rpx)")

    # 内边距推断：仅当某方向差值较小（内边距而非大留白）时注解。
    # 纯图标容器的"留白"是图标视觉内边距，非布局 padding，跳过。
    # 叠层父节点（遮罩+弹窗）的子节点是绝对定位/居中，padding 概念不适用，
    # 且坐标法会从内容容器位置推算出误导值（曾输出 左39/右40 与全屏遮罩矛盾），
    # 一并跳过；内容容器位置已在实现要点的弹窗遮罩结构说明中给出。
    # FIXED/FILL 且内容居中的容器：padding 字段值被居中布局吸收（按钮字段 10、
    # 实测左右 60/上下 14），照字段值实现会丢响应式语义。改输出实测内容距边
    # （不受 PADDING_INFER_MAX 限制——它就是「padding+内容撑出尺寸」所需的
    # 正确值），尾注给出两种自洽实现方案。
    al_pad = get_autolayout(node)
    sizing_v = (al_pad or {}).get("sizing_v", "")
    sizing_h = (al_pad or {}).get("sizing_h", "")
    if _is_icon_container(node) or _overlay_children(node):
        pad = {"vertical": {"top": 0, "bottom": 0, "valid": False},
               "horizontal": {"left": 0, "right": 0, "valid": False}}
    else:
        pad = _infer_padding(node)
    mi = _measured_content_inset(node)
    if pad["vertical"]["valid"]:
        vt, vb = pad["vertical"]["top"], pad["vertical"]["bottom"]
        if sizing_v == "FIXED":
            if mi:
                vt, vb = mi["top"], mi["bottom"]
            tail = (f"（容器高 {_fmt_px(ht)} = 此实测距离 + 内容高：实现可用 padding 上下 {vt}/{vb} + 内容撑开"
                    f"（随文案/字号适应，推荐），或写死高 {_fmt_px(ht)} + 内容垂直居中）")
        elif sizing_v == "FILL":
            if mi:
                vt, vb = mi["top"], mi["bottom"]
            tail = "（容器高度填满父容器：此为内容到边缘的实测距离，实现用 padding 此值 + 内容垂直居中）"
        elif vt == vb:
            tail = "（容器高度由子元素高度 + 上下 padding 撑开）"
        else:
            tail = "（上下 padding 不等，容器高度由子元素 + padding 撑开）"
        lines.append(f"{anno_prefix}- [内边距] 上 {vt}px / 下 {vb}px{tail}")
    if pad["horizontal"]["valid"]:
        vl, vr = pad["horizontal"]["left"], pad["horizontal"]["right"]
        if sizing_h == "FIXED":
            if mi:
                vl, vr = mi["left"], mi["right"]
            tail = (f"（容器宽 {_fmt_px(w)} = 此实测距离 + 内容宽：实现可用 padding 左右 {vl}/{vr} + 内容撑开"
                    f"（随文案适应，推荐），或写死宽 {_fmt_px(w)} + 内容水平居中）")
        elif sizing_h == "FILL":
            if mi:
                vl, vr = mi["left"], mi["right"]
            tail = (f"（容器宽度在父内填满/均分（节点宽 {_fmt_px(w)}）：此为内容到边缘的实测距离；"
                    f"实现建议 padding 左右 {vl}/{vr} + 内容水平居中，内容随文案伸缩、勿写死宽，"
                    f"在父内以弹性布局均分）")
        elif vl == vr:
            tail = "（容器宽度由子元素宽度 + 左右 padding 撑开）"
        else:
            tail = ""
        lines.append(f"{anno_prefix}- [内边距] 左 {vl}px / 右 {vr}px{tail}")

    children = merge_repeats(raw_children)
    sorted_children = sort_children(children)

    # 水平居中旗标：仅当父容器 autoLayout 显式声明居中（VERTICAL 的交叉轴 /
    # HORIZONTAL 的主轴 = CENTER）时输出，不从 bounds 猜测——手动摆放在恰好
    # 对称时会被误判为居中意图。inset 由 bounds 计算供实现直接取值。
    al_parent = get_autolayout(node)
    h_center = (
        al_parent is not None
        and ((al_parent.get("layout_mode") == "VERTICAL" and al_parent.get("counter_align") == "CENTER")
             or (al_parent.get("layout_mode") == "HORIZONTAL" and al_parent.get("primary_align") == "CENTER"))
    )

    for idx, child in enumerate(sorted_children):
        child_path = f"{path} > {child.get('name', '')}" if path else child.get("name", "")
        if reusable_ref and child_path in reusable_ref:
            connector = "└── " if idx == len(sorted_children) - 1 else "├── "
            # 引用值已是「代表实例名 + 尺寸」，含尺寸可在同名不同结构的实例间
            # 唯一定位（如 Frame 427321777 245×20px 与 245×46px 是两种结构）。
            # 补充「组内文字同代表」：分组签名含文字内容，被折叠实例的文案
            # 必与代表一致——实现者无法看到被折叠实例的文字，连续两轮审查都
            # 担心照抄代表文案出错，此处显式声明消除疑虑。
            rep_ref = reusable_ref.get(child_path) or ""
            hint = (f"（完整结构见「{rep_ref}」的完整展开处，组内文字同代表）"
                    if rep_ref else "")
            lines.append(f"{child_prefix}{connector}[复用] {child.get('name', '')} — 结构与同类实例一致{hint}")
            continue
        center_flag = ""
        if h_center:
            cbn = child.get("bounds", {})
            pbn = node.get("bounds", {})
            inset_l = cbn.get("left", 0) - pbn.get("left", 0)
            inset_r = (pbn.get("left", 0) + pbn.get("width", 0)) - (cbn.get("left", 0) + cbn.get("width", 0))
            if inset_l > 0.5 and inset_r > 0.5:
                # 居中保证两侧近似相等，取左侧 inset 输出；子元素撑满（inset≈0）不标
                center_flag = f"水平居中于父: {_fmt_px(inset_l)}px"
        _render(child, lines, depth + 1, path=child_path, reusable_ref=reusable_ref,
                prefix=child_prefix, is_last=(idx == len(sorted_children) - 1),
                center_flag=center_flag, parent_bounds=node.get("bounds", {}))


def _overlay_children(node):
    """判断节点的直接子节点是否构成「全屏半透明遮罩 + 浮于其上的内容容器」叠层。

    返回 (overlay_node, [content_nodes]) 或 None。判据完全来自可确定性数据：
    遮罩 = 尺寸覆盖父节点 ≥95% 且填充带 alpha（rgba）的兄弟；内容容器 = 其余
    显著小于父节点的兄弟。供 _render（布局旗标）与 _detect_modal_overlay
    （实现要点）共用，避免两处口径分歧。
    """
    children = node.get("children", [])
    bn = node.get("bounds", {})
    pw, ph = bn.get("width", 0), bn.get("height", 0)
    if len(children) < 2 or not pw or not ph:
        return None
    overlay = None
    contents = []
    for c in children:
        cb = c.get("bounds", {})
        cw, ch = cb.get("width", 0), cb.get("height", 0)
        fills = c.get("fills") or []
        if cw >= pw * 0.95 and ch >= ph * 0.95 and any("rgba" in f for f in fills):
            overlay = c
        elif cw < pw * 0.95:
            contents.append(c)
    if not overlay or not contents:
        return None
    return overlay, contents


def _detect_modal_overlay(tree):
    """检测弹窗遮罩模式，返回实现要点文案或 None（判据见 _overlay_children）。

    结构树把遮罩与弹窗画成垂直兄弟，按序实现会排成上下排列（实现者评审实测的
    最大歧义点）；_render 已据 _overlay_children 把该节点标为「叠层」，此处再
    给出位置说明（内容容器垂直居中/距顶），两处配合消除歧义。
    """
    found = _overlay_children(tree)
    if not found:
        return None
    overlay, contents = found
    ph = tree.get("bounds", {}).get("height", 0)
    oname = overlay.get("name", "")
    parts = []
    for c in contents:
        cb = c.get("bounds", {})
        top = cb.get("top", 0)
        ch = cb.get("height", 0)
        bottom = ph - top - ch
        cname = c.get("name", "")
        if abs(top - bottom) <= 4 and top > 0:
            pos = f"垂直居中（上下各{_fmt_px(top)}px）"
        else:
            pos = f"距顶{_fmt_px(top)}px"
        parts.append(f"「{cname}」{pos}浮于遮罩「{oname}」之上")
    return (
        "**弹窗遮罩结构**：" + "；".join(parts) +
        "。两者在树中是兄弟节点，实现为叠层（遮罩全屏铺底、内容容器按上述位置"
        "绝对定位/居中浮于其上），不要按树顺序实现成上下排列。"
    )


def _detect_hidden_state_variants(tree):
    """检测同名节点「可见/不可见实例并存」：可能对应选中态等状态差异。

    仅陈述可观察事实（并存 + 不可见已标跳过），语义判断显式交还给设计确认，
    不臆造。返回提示文案或 None。
    """
    visible, hidden = set(), set()

    def walk(n):
        name = n.get("name", "")
        if name:
            if n.get("opacity", 1) <= 0.01:
                hidden.add(name)
            else:
                visible.add(name)
        for c in n.get("children", []):
            walk(c)

    walk(tree)
    both = sorted(visible & hidden)
    if not both:
        return None
    names = "、".join(f"「{n}」" for n in both[:3])
    return (
        f"**状态差异提示**：{names}等节点同时存在可见与不可见实例（不可见者已标"
        " [不可见] 不渲染但保留占位）。这可能对应选中态等交互状态，语义请与设计确认后实现，勿臆造。"
    )


def _generate_impl_notes(tree):
    """生成实现要点。

    默认原则（容器尺寸用 padding 撑开、不写死宽高）恒定输出；其余为跨节点/
    行为级约束的按页检测结果（如弹窗遮罩叠层）。节点级设计事实均在「页面结构」
    树的旗标/标注中给出，不在此附加主观建议。
    """
    notes = []
    modal_note = _detect_modal_overlay(tree)
    if modal_note:
        notes.append(modal_note)
    hidden_note = _detect_hidden_state_variants(tree)
    if hidden_note:
        notes.append(hidden_note)
    default_principle = (
        "**容器尺寸与 padding 规则（按树内 [内边距] 尾注对号入座）**。"
        "总原则：所有容器优先用「padding + 内容」撑出尺寸、勿写死宽高——"
        "文案变长或字号差异时自动适应，跨设备表现一致；仅用户明确要求时才写死。"
        "① 尾注「撑开」→ 该方向不写死：用 [内边距] 精确值（见标注）+ 内容自然撑开。"
        "② 尾注「固定值」→ [内边距] 已是内容到边缘的实测距离：推荐 padding 此值 + 内容撑开"
        "（随文案适应），或写死该方向尺寸 + 内容居中——两种都能还原设计值。"
        "③ 尾注「填满父容器」→ [内边距] 同为实测距离：推荐 padding 此值 + 内容居中"
        "（内容随文案伸缩），在父容器内以弹性布局排列，勿按节点宽写死。"
        "无 [内边距] 标注的节点按自身标注实现即可。任何偏离上述规则先与用户确认。"
    )
    notes.append(default_principle)
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
    p.add_argument("-o", "--output", help="输出文件路径（默认按输入 JSON 文件名生成，避免同页多稿互相覆盖）")
    p.add_argument("-w", "--width", type=int, default=375, help="设计图宽度（默认 375px）")
    p.add_argument("-n", "--name", help="自定义输出文件名（不含扩展名，自动补 .md 并落到 design-analyzer/ 目录）；不指定则默认用输入 JSON 文件名")
    args = p.parse_args()

    with open(args.file) as f:
        flat_nodes = json.load(f)

    # 摹客 JSON 是扁平列表，需要转换为嵌套树
    root = build_tree_from_flat_list(flat_nodes)

    if args.output:
        # -o 指定完整输出路径（最高优先级，保持原有行为）
        if args.name:
            # -o 与 -n 同时传入时 -o 优先，-n 被忽略，提示避免困惑
            print("⚠️  同时指定了 -o/--output 与 -n/--name，以 -o 为准，-n 已忽略",
                  file=sys.stderr)
        out_file = args.output
    else:
        # 默认输出到「用户当前工作目录」下的 design-analyzer/ 子目录，自动创建。
        # 注意：这里是用户运行命令时所在的目录（即项目目录），而非脚本/skill 所在目录。
        out_dir = os.path.join(os.getcwd(), "design-analyzer")
        os.makedirs(out_dir, exist_ok=True)
        # 输出文件名规则（优先级从高到低）：
        #   1. -n/--name 自定义
        #   2. 默认用「输入 JSON 文件名（去 .json）」
        # 默认不用 JSON 内部的 root.name 做文件名：同一页面在不同状态下会有多张设计稿
        # （如「指定销司」「指定门店」「暂无样机标准数据」），它们的 root.name 往往相同
        # （如都是「审批详情-样机标准申请」），用 root.name 会互相静默覆盖、丢失有用稿；
        # 而 JSON 文件名本身已带状态区分，与其一一对应，天然不重名，用户手工归档也多用此命名。
        if args.name:
            base = args.name
        else:
            base = os.path.splitext(os.path.basename(args.file))[0]
        out_file = os.path.join(out_dir, f"{base}.md")

    md = generate(root, args.width)

    parent = os.path.dirname(out_file)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_file, "w") as f:
        f.write(md)
    print(f"✅ 规格文档已生成: {out_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
