# design-analyzer

从**摹客设计稿 JSON** 生成规格文档。


## 怎么用

1. **导出物料**：在摹客打开设计稿，点击右上角下载按钮 →「下载本页图层数据」→「下载本页所有图层数据」，得到 JSON 文件。

   ![下载摹客 JSON](./images/download-mockplus-json.png)
2. **触发 skill**：把 JSON 文件的路径告诉 agent，例如"用 design-analyzer skill 分析 `/path/to/design.json`"。如有多个 JSON，逐个指明要分析的那个即可。

剩下的（生成文档、实现页面）由 agent 完成，无需你动手。

## 输出在哪

规格文档默认生成在**当前项目目录**下的 `design-analyzer/spec_页面名.md`（目录不存在会自动创建）。例如你在 `my-app/` 中触发 skill，文档就落在 `my-app/design-analyzer/`，不会放到 skill 自身目录。

## 只做部分 UI

不需要整页实现时，直接跟 agent 说按规格文档做局部即可，例如：

- "根据 `spec_xxx.md` 实现其中的搜索栏"
- "参考 `spec_xxx.md` 修改首页的卡片列表样式"

agent 会读取对应规格文档，只处理你指定的那部分 UI。
