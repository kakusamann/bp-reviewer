# BP-Reviewer v5 — Claude Code 用户验收说明

## 目标

从**科研用户**视角确认 BP-Reviewer 已从“项目文件图”升级为“可解释的功能和实验工作流”。用全新合成示例检查核心能力，再用实际 AstroLCA 项目只读检查。请勿对被分析目标项目执行安装、训练、编译或修改。

## 环境与运行

在 BP-Reviewer 根目录（`main` 分支）：

```powershell
git switch main
git pull --ff-only origin main
python -m pip install -e "./backend[dev]"
python -m pytest -q
cd frontend
npm ci
npm test -- --run
npm run build
cd ..
```

使用项目自带的 `start.ps1` / `stop.ps1` 启动本地页面。在允许扫描的目录中选择 `examples/research_workflow_sample`；如系统仅允许父目录，请按原有启动说明将**扫描根目录**设置为 BP-Reviewer 仓库的父目录或仓库本身，不要改变服务器访问控制来绕过白名单。配置 MiMo API 才执行真实 AI 蓝图生成；无 API 时仍可运行自动化的确定性测试。

## 用户任务（逐项记录通过/失败和截图）

1. **扫描和总览**：导入上述示例，在全局蓝图确认出现知识图谱补全、背景库匹配和 LCIA 等功能域（允许语义命名差异，不要求写死名称）；根层不应被大量原始文件占据。
2. **动态递归**：全局构建后记录识别总数、成功、失败和失败原因；代码不应截取固定 8/12 个业务模块，也不能把失败当作成功。小项目的单文件功能域亦应可以进入内部。
3. **KGC 工作流**：进入 KGC，确认能看到真实证据支持的输入/数据契约、准备、模型选择、训练执行、性能评价、结果输出等步骤；**不能为没有源码依据的步骤虚构已验证连接**。
4. **模型选型**：在“技术与参数”确认可用候选模型 TransE、RotatE、RGCN（依据 `src/lca_lab/kgc/registry.py` 中的 `MODEL_REGISTRY`）。
5. **当前参数**：确认能看到模型 `RotatE`、维度 200、学习率 0.001、epoch 15（来自 `config/experiment.json`）及其可跳转的来源；区分模型注册候选与已配置值。
6. **运行结果**：在“结果与指标”看到仅 **RotatE / demo-run-001** 的合成记录及 MRR 0.37、Hits@1 0.24、Hits@3 0.42、Hits@10 0.63。不要将 TransE/RGCN 误报为已运行。
7. **配置草案**：在“技术与参数”中将模型选为 TransE（只生成拟议调整），把训练 epoch 从 15 改为 20；检查生成的 JSON 变更草案包含来源文件与原始值，但 `config/experiment.json` 的 sha256 不变。草案不代表实验已运行。
8. **Inspector**：正常浏览时默认展示模块职责、步骤、配置、指标和来源；原始 `ports` / `parameters` JSON 只能藏在“开发者详情”下，不得占据主要交互界面。
9. **局部重建**：从 KGC 内部重建，确认兄弟模块（背景匹配、LCIA）的 ID、父子关系和证据不被改变；截图记录成功/失败和提示信息。
10. **只读保证**：扫描前后用 Git 或 sha256 比较 **示例项目所有原始文件**，不应有修改；拖动事实图只保存浏览器布局，不能修改来源。
11. **深色模式与操作**：测试节点标题、Inspector、长文件路径、双击进入和返回、配置与指标点击跳转、构建进度弹窗。
12. **真实项目复测**：在 AstroLCA 测试一次 KGC 的层级、模型注册/选型、配置和结果定位；记录语义错误及不可识别项目。真实项目可能没有可解析的指标工件，系统应明确展示“未找到”，不能编造运行结果。

## 确定性回归测试（无需 MiMo Token）

```powershell
python -m pytest tests/test_experiment_profile.py tests/test_hierarchical_blueprint.py tests/test_scoped_blueprint.py -q
```

## 给 Claude Code 的验收指令（可复制）

> 请作为 BP-Reviewer 的使用者和质量审查员，严格遵守只读目标项目约束。在 `main` 分支执行本项目自动化测试和前端构建；依照 `docs/claude-code-v5-acceptance.md` 使用 `examples/research_workflow_sample` 验收全局功能分组、KGC 内部工作流、模型注册、参数配置、只读配置调整草案、历史结果、证据跳转、局部重建、深色模式与事实图拖动。请区分“代码中可用”、“配置中选择”、“存在历史结果记录”、“实际运行得到验证”。不得通过执行合成目标代码绕开静态分析。对每项给出 PASS/FAIL、截图或文件路径证据、复现步骤、严重级别（P0/P1/P2），最后生成一个独立的 `docs/v5-acceptance-report.md` 报告。未经我批准，不要修改 BP-Reviewer 的业务代码，也不要修改被分析目标项目。

## 验收记录格式

| 编号 | 结果 PASS/FAIL | 截图/输出依据 | 实际表现 | 期望表现 | 严重性 |
| --- | --- | --- | --- | --- | --- |
| 01 |  |  |  |  |  |

报告最后列出：构建任务 ID、识别到的功能域数、成功/失败/未执行数、模型名称、可定位参数数、可定位结果数、出错堆栈或 warnings（注意删除 Token）、复现命令以及原始示例目录 hash 前后对照。

**限制：** 此样例以静态识别逻辑验证为主。MiMo 的语义命名存在模型/版本相关差异，不要求固定中文模块名称；真实的运行数据不允许凭空推断。
