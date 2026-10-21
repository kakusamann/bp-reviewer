# BP-Reviewer frontend

React Flow 多端口画布、Dagre 布局、Inspector 和可撤销草稿编辑器。`src/editor.ts` 将原工作台的连接校验与历史逻辑抽象为通用图；`src/App.tsx` 提供模块展开、证据查看、只读 As-is、独立 Proposed、Diff 和 JSON/Markdown 下载。

Node `24.15.0`、npm `11.16.0`；`npm ci`、`npm test`、`npm run build`、`npm run dev`。开发 UI 仅绑定 `127.0.0.1:5174`，将 `/api` 代理到独立的 `127.0.0.1:8011` 后端。本地令牌放在 sessionStorage；浏览器恢复草稿以快照 ID 隔离，不放目标源码目录。
