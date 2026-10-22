# BP-Reviewer frontend

React Flow 多端口画布、Dagre 布局、Inspector 和可撤销草稿编辑器。`src/editor.ts` 将原工作台的连接校验与历史逻辑抽象为通用图；`src/App.tsx` 提供模块展开、证据查看、只读 As-is、独立 Proposed、Diff 和 JSON/Markdown 下载。

Proposed 卡片与连线均显示拟议状态，避免把编辑后的参数/关系冒充基线事实。拖动节点使用 React Flow 节点变更回调更新画布；拖动结束才写入可撤销草稿。

Node `24.15.0`、npm `11.16.0`；`npm ci`、`npm test`、`npm run build`、`npm run dev`。开发 UI 仅绑定 `127.0.0.1:5174`，将 `/api` 代理到独立的 `127.0.0.1:8011` 后端。本地令牌放在 sessionStorage；浏览器恢复草稿以快照 ID 隔离，不放目标源码目录。也可在仓库根目录用 `.\start.ps1` 一键启动前后端。

实际 Edge 验收覆盖拖线、断开、A→B 重连、撤销/重做、保存草稿后重新加载页面、As-is 隔离、下载 JSON/Markdown、层级下拉框展开函数、源码证据及缩放。截图和验收范围见 [截图说明](../docs/screenshots/README.md)。组件测试与实际指针操作分别记录，不以组件测试代替浏览器验收。
