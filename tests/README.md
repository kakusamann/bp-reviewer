# 验证

在 `bpreviewer` 环境运行 `python -m pytest -q`。测试覆盖只读 AST、源码哈希、manifest、静态/声明证据、未知类型、合同、方向/基数/必填/DAG、A→B 重连差异、布局忽略、草稿修订冲突、多个项目隔离、访问边界和源码证据过期提示。符号链接测试在 Windows 权限不足时明确跳过。

测试使用临时目录与合成示例，不访问科研数据，不执行目标脚本。环境独立性测试要求未安装 `astralca` 和科研框架。

## 一键启动脚本检查

`tests/test_startup_scripts.py` 覆盖 `start.ps1`、`stop.ps1`、`start.bat`：

- 静态检查（全平台）：交付文件存在、`start.bat` 仅做薄包装、UTF-8 BOM、PowerShell 语法解析、端口与 `package.json`/Vite 代理/`.nvmrc` 一致、后端参数与 `cli.py` 一致、仅回环绑定、无硬编码令牌/破坏性 Git/自动安装、写入不触及扫描目标、错误提示含修复命令。
- 场景检查（Windows + PowerShell，自动运行）：目标目录缺失、状态目录落入目标、conda 缺失、`bpreviewer` 环境缺失、Node 版本不符、端口被占用（不杀进程）、`npm ci` 失败不启动前端。这些用 PATH 桩替身执行真实脚本，不启动真实服务。
- 完整启动/停止集成（可选）：`BPREVIEWER_LAUNCH_TESTS=1 python -m pytest tests/test_startup_scripts.py -v` 会真实绑定 8011/5174，启动后端与前端、经 Vite 代理带令牌访问 API、扫描目标并校验指纹不变、执行 stop.ps1 并确认端口释放、启动记录清理、扫描快照保留。运行前请先停止其他实例。

尚需人工在 Windows 验证：默认 `..\AstroLCA` 启动（存在同级目录时）、浏览器自动打开/`-NoBrowser`、在前端页面输入后端终端显示的令牌、`start.bat` 双击入口、真实 AstroLCA 前后指纹（也可用 `python scripts/acceptance.py --project ../AstroLCA` 复核）。
