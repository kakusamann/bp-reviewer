# BP-Reviewer backend

`bpreviewer.models` 管理通用 manifest/graph；`scanner` 只读 AST，跳过数据、缓存、隐藏目录与链接；`service` 将不可变快照和带修订号的草稿写入独立状态目录；`review` 做端口约束和确定性差异；`api` 提供本地令牌鉴权，`cli` 固定 loopback 启动。

不导入目标项目，不接收目标命令，不写目标文件。启动、限制和 API 用法见项目 README 与 `docs/manifest.md`。


Semantic blueprint analysis is orchestrated in `bpreviewer.agent` with LangGraph. It ranks candidate files, sends bounded excerpts to MiMo, validates returned paths, and stores generated graphs in the separate BP-Reviewer state directory. See [semantic-agent documentation](../../docs/semantic-agent.md) and the repository-root `.env.example`.
