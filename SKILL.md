---
name: design-analyzer
description: 精确分析 UI 设计截图——提取文字、色值、间距、组件层级。当用户上传设计稿、截图，或说「帮我看看这个设计图」「分析这张截图」「这个页面的颜色/字号/间距是多少」时触发。也适用于需要精确还原设计稿到代码的场景。支持摹客图层 JSON 和截图两种输入。
---

# 设计图精确分析

## 数据源分工

| 数据 | 来源 | 可信度 |
|------|------|--------|
| 色值、坐标、尺寸、字号、圆角、间距 | **JSON** | ✅ 100% 可信 |
| 布局方向 | **JSON** 坐标推断 | ✅ 可信 |
| 文字内容 | **JSON** + 截图交叉验证 | ⚠️ JSON 的 `text.styles[0].value` 是实际文字；`basic.name` 是组件名不可信 |

## 分析流程

### Step 1：用 JSON 生成规格文档

```bash
python3 scripts/generate_spec.py layerData.json
```

此时当前目录生成 `spec_页面名.md`，包含：
- 完整区块层级树
- 每个元素的颜色、字号、圆角、描边
- 子元素间距（自动排序后计算）
- 颜色表（含推测用途）
- 截图文字确认清单

### Step 2：交叉验证文字

规格文档末尾有一个「⚠️ 截图确认清单」表格。对照设计截图，确认 JSON 文字是否正确。

- 匹配 → 无需处理
- 不匹配 → 以截图为标准
- 模糊 → 标注需确认

### Step 3：补充组件拆分建议

根据文档中的区块层级 + 截图，判断哪些区块应抽成独立组件。

### Step 4：等待用户确认后实现

把规格文档 + 组件建议给用户。用户确认无误后，按文档精确值写代码。

## 脚本一览

| 脚本 | 用途 | 命令 |
|------|------|------|
| `generate_spec.py` | **一键生成规格文档** | `python3 scripts/generate_spec.py layerData.json` |
| `parse_layer_json.py` | 图层详情（含文字、颜色、间距） | `python3 scripts/parse_layer_json.py layerData.json [--search 关键词] [--range top1 top2] [--summary]` |
| `ocr_analyze.py` | 截图 OCR + 取色（无 JSON 时兜底） | `python3 scripts/ocr_analyze.py 设计图.png --crop left,top,right,bottom` |

## 关键经验（防坑）

1. **摹客 JSON 文字在 `text.styles[0].value`**，不是 `text.characters`。`basic.name` 是组件默认名不可信。
2. **文字颜色在 `text.styles[0].font.color.value`**，不在 `fill.colors`。
3. **间距计算必须按 y 坐标排序**，JSON 的子元素顺序 ≠ 视觉顺序。
4. **合并重复结构时签名要包含 type**，否则相同底色的不同区块会被误合并。
5. **规格文档默认输出到当前目录**，文件名 `spec_页面名.md`。
