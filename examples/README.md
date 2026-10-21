# 合成示例

`tiny_pipeline` 是可自由用于本仓库测试的自建示例，包含两个 Python 模块及可选 manifest。静态导入/调用来自 AST；载入到汇总的数值流来自 manifest，标记为 DECLARED。没有运行指标或外部数据。

扫描该目录，在 Proposed 将 `logical:load:value` 的连线由 `logical:summary:A` 重连到 `B`，然后导出 Change Request。导出必须分别出现 remove_edge/add_edge；原示例文件保持不变。
