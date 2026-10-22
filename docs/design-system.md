# Blueprint Studio 视觉设计规范（迭代提案）

## 目标
统一 BP-Reviewer 的工作台视觉语言，为下一阶段的“真实目录树 / 逻辑模块 / 数据流 / Agent 分析”提供可复用的设计基础。当前提交只实现视觉基础，不宣称已实现 Agent 或目录联动功能。

## 设计参考
- [Radix Colors](https://www.radix-ui.com/colors/docs/palette-composition/scales)：多级中性色、语义强调色以及深色背景对比。
- [React Flow examples](https://reactflow.dev/examples)：节点编辑器、Dark Mode、嵌套子流程与连接交互。
- [React Flow dark mode](https://reactflow.dev/examples/styling/dark-mode)：使用 `colorMode="dark"` 与现有画布组件协同。

## 颜色语义（CSS variables）
| Token | 色值 | 用途 |
| --- | --- | --- |
| `--app-bg` | `#0c1018` | 最底层工作区 |
| `--surface-0` | `#111723` | 面板与工具条 |
| `--surface-1` | `#151d2b` | 浮层/突出内容 |
| `--surface-2` | `#1c2636` | 操作与卡片 |
| `--text` | `#e8eef9` | 正文 |
| `--muted` | `#9aa8be` | 次要描述 |
| `--accent` | `#889aff` | 主操作、选中、高亮 |
| `--data` | `#4ad4c0` | 数据流 |
| `--control` | `#b4a1ff` | 控制流 |
| `--warning` | `#f6bf6f` | 未确认、警告 |
| `--danger` | `#ff8f95` | 错误 |

不要仅依靠颜色传递边的语义。仍应显示关系文字，控制流可辅助使用虚线区分。

## 后续 UI 集成要求
1. 左侧真实目录树：目录层级缩进，文件类型图标，路径选中态，定位并滚动到节点，点击文件查看只读源码。
2. 中央逻辑蓝图：根级只展示聚合模块，逐层展开；模块外围保留输入/输出端口；跨层折叠时聚合连线。
3. 右侧 Inspector：模块摘要、证据文件路径、来源状态、数据合同和置信度独立分组；未知证据不得被展示为“已验证”。
4. Agent 状态：对静态事实、LLM 推断、人工声明和运行证据使用文本状态与一致的颜色语义。
5. 响应式：窄屏时转为纵向区块；保持键盘焦点可见，尊重 `prefers-reduced-motion`。

## 实施边界
本次视觉改造不修改扫描器、目标文件或模型配置。后续 LangGraph 工作流、小米 MiMo 环境变量与项目目录双向联动均保留既定方案，须分别实现并测试。
