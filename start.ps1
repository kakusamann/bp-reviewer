# BP-Reviewer local one-click start (Windows). Read-only toward target projects.
# Flow: resolve paths -> validate scan roots -> validate state dir -> conda -> Node/npm
#       -> port check -> backend window -> frontend window -> readiness -> summary.
# Never executes target code, never binds public interfaces, never writes outside repo state/.

[CmdletBinding()]
param(
    # Target project to scan; defaults to the sibling directory ..\AstroLCA.
    [string]$ProjectPath,
    # Do not open the browser automatically.
    [switch]$NoBrowser,
    # Force npm ci in frontend/ even when node_modules already exists.
    [switch]$ReinstallFrontendDependencies
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Fail {
    param([string]$Message)
    Write-Host ''
    Write-Host ('ERROR: ' + $Message) -ForegroundColor Red
    exit 1
}

function ConvertTo-SingleQuotedLiteral {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Test-DirectoryContains {
    # True when $Child is $Parent itself or lies beneath it.
    param([string]$Parent, [string]$Child)
    $parentTrimmed = $Parent.TrimEnd('\')
    $childTrimmed = $Child.TrimEnd('\')
    if ($childTrimmed.Equals($parentTrimmed, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    return $childTrimmed.StartsWith($parentTrimmed + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-TcpListening {
    param([string]$Address, [int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect($Address, $Port)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-TcpListening {
    param([string]$Address, [int]$Port, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpListening -Address $Address -Port $Port) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Wait-HttpResponse {
    # Any HTTP answer (including 4xx/5xx) counts as "service is up"; the API is token-protected.
    param([string]$Url, [int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 | Out-Null
            return $true
        } catch {
            if ($_.Exception.PSObject.Properties['Response'] -and $_.Exception.Response) {
                return $true
            }
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

if ($env:OS -ne 'Windows_NT') {
    Write-Fail 'start.ps1 仅支持 Windows。'
}

# ---- Step 1: resolve repo paths from $PSScriptRoot (never rely on the caller's cwd) ----
$RepoRoot = $PSScriptRoot
$FrontendDir = Join-Path $RepoRoot 'frontend'
$PackageJson = Join-Path $FrontendDir 'package.json'
$PackageLock = Join-Path $FrontendDir 'package-lock.json'
$ExampleRoot = Join-Path (Join-Path $RepoRoot 'examples') 'tiny_pipeline'
$EnvironmentFile = Join-Path $RepoRoot 'environment.yml'
$StateDir = Join-Path $RepoRoot 'state'
$DefaultTarget = Join-Path (Split-Path -Parent $RepoRoot) 'AstroLCA'

$requiredPaths = @{
    $RepoRoot        = 'BP-Reviewer 仓库根目录（脚本必须位于仓库根目录）'
    $FrontendDir     = '前端目录'
    $PackageJson     = '前端依赖声明'
    $PackageLock     = '前端依赖锁文件'
    $ExampleRoot     = '合成示例扫描根目录'
    $EnvironmentFile = 'Conda 环境声明'
}
foreach ($entry in $requiredPaths.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $entry.Key)) {
        Write-Fail ("必需路径不存在: {0}`n说明: {1}`n修复: 请恢复该路径后重试（从完整克隆的 bp-reviewer 仓库根目录运行本脚本）。" -f $entry.Key, $entry.Value)
    }
}
if (-not (Test-Path -LiteralPath $ExampleRoot -PathType Container)) {
    Write-Fail ("必需目录不存在或不是目录: {0}`n修复: 请恢复 examples/tiny_pipeline 后重试。" -f $ExampleRoot)
}

# ---- Step 2: validate the target project and build the allow-root list ----
if ($ProjectPath) {
    $TargetPath = $ProjectPath
} else {
    $TargetPath = $DefaultTarget
}
if (-not (Test-Path -LiteralPath $TargetPath)) {
    if ($ProjectPath) {
        Write-Fail ("目标目录不存在: {0}`n修复: 传入已存在的目录，例如 .\start.ps1 -ProjectPath `"D:\path\to\project`"" -f $TargetPath)
    }
    Write-Fail ("默认目标目录不存在: {0}`n修复: 请显式指定目标目录，例如 .\start.ps1 -ProjectPath `"D:\path\to\project`"（不扫描错误目录）。" -f $DefaultTarget)
}
if (-not (Test-Path -LiteralPath $TargetPath -PathType Container)) {
    Write-Fail ("目标路径不是目录: {0}`n修复: 传入一个已存在的目录。" -f $TargetPath)
}
$TargetResolved = (Resolve-Path -LiteralPath $TargetPath).ProviderPath
$TargetResolved = [System.IO.Path]::GetFullPath($TargetResolved)

# Only the synthetic example and the target project are authorized; never the workspace parent.
$allowRoots = New-Object System.Collections.Generic.List[string]
foreach ($candidate in @((Resolve-Path -LiteralPath $ExampleRoot).ProviderPath, $TargetResolved)) {
    $covered = $false
    foreach ($existing in $allowRoots) {
        if (Test-DirectoryContains -Parent $existing -Child $candidate) {
            $covered = $true
            break
        }
    }
    if (-not $covered) {
        foreach ($existing in @($allowRoots)) {
            if (Test-DirectoryContains -Parent $candidate -Child $existing) {
                [void]$allowRoots.Remove($existing)
            }
        }
        $allowRoots.Add($candidate)
    }
}

# ---- Step 3: validate the state directory (repo-local, outside every scan root) ----
$StateResolved = [System.IO.Path]::GetFullPath($StateDir)
foreach ($root in $allowRoots) {
    if (Test-DirectoryContains -Parent $root -Child $StateResolved) {
        Write-Fail ("状态目录不得位于扫描目标内部: 状态目录 {0} 在目标 {1} 内。`n原因: BP-Reviewer 的快照、草稿和日志只写入独立状态目录，绝不写入被扫描项目。`n修复: 请改用其他目标目录，或在仓库外运行扫描服务时将仓库整体移出目标目录。" -f $StateResolved, $root)
    }
}
if (-not (Test-Path -LiteralPath $StateResolved)) {
    New-Item -ItemType Directory -Path $StateResolved | Out-Null
}
$RuntimeDir = Join-Path $StateResolved 'runtime'
if (-not (Test-Path -LiteralPath $RuntimeDir)) {
    New-Item -ItemType Directory -Path $RuntimeDir | Out-Null
}

Write-Host '== BP-Reviewer 启动检查 =='
Write-Host ('仓库根目录: ' + $RepoRoot)
Write-Host ('扫描目标:   ' + $TargetResolved)
Write-Host ('授权根目录: ' + ($allowRoots -join '; '))
Write-Host ('状态目录:   ' + $StateResolved)

# ---- Step 4: Conda environment check (bpreviewer only; never ch4kg/astrolca) ----
$conda = Get-Command conda -ErrorAction SilentlyContinue
if (-not $conda) {
    Write-Fail ("未找到 conda 命令。`n修复: 请安装 Anaconda/Miniconda 并将其加入 PATH 后重试。BP-Reviewer 使用独立的 bpreviewer 环境，不依赖 ch4kg 或 astrolca 环境。")
}
$envListRaw = ''
$envListLines = @()
try {
    $envListLines = @(& conda env list --json 2>&1)
    $envListRaw = ($envListLines | Out-String)
} catch {
    Write-Fail ("无法执行 conda env list: {0}" -f $_.Exception.Message)
}
if ($LASTEXITCODE -ne 0) {
    Write-Fail ("conda env list 失败: {0}" -f $envListRaw.Trim())
}
$envFound = $false
try {
    $parsed = $envListRaw | ConvertFrom-Json
    foreach ($envPath in @($parsed.envs)) {
        if ((Split-Path -Leaf $envPath) -eq 'bpreviewer') {
            $envFound = $true
            break
        }
    }
} catch {
    Write-Fail ("无法解析 conda env list --json 输出: {0}" -f $_.Exception.Message)
}
if (-not $envFound) {
    Write-Fail ("Conda 环境 bpreviewer 不存在。`n修复: 在仓库根目录执行: conda env create -f environment.yml`n（本工具不使用 ch4kg 或 astrolca 环境。）")
}
$probeLines = @()
try {
    $probeLines = @(& conda run -n bpreviewer python -c "import bpreviewer, fastapi, pydantic, uvicorn" 2>&1)
} catch {
    $probeLines = @($_.Exception.Message)
}
if ($LASTEXITCODE -ne 0) {
    $probeText = ($probeLines | Out-String).Trim()
    Write-Fail ("bpreviewer 环境缺少后端依赖（bpreviewer/fastapi/pydantic/uvicorn 导入检查未通过）。`n探测输出:`n{0}`n修复: 在仓库根目录执行:`n  conda run -n bpreviewer python -m pip install -e `".\backend[dev]`"" -f $probeText)
}

# ---- Step 5: Node.js / npm check ----
foreach ($tool in @('node', 'npm')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Fail ("未找到 {0} 命令。`n修复: 安装 Node.js 24.15.0（含 npm 11.16.0）并将其加入 PATH 后重试。检查方式: {0} -v" -f $tool)
    }
}
$nodeVersion = (@(& node -v 2>&1) | Out-String).Trim()
$npmVersion = (@(& npm -v 2>&1) | Out-String).Trim()
if ($nodeVersion -ne 'v24.15.0') {
    Write-Fail ("Node.js 版本不匹配。`n检测值: {0}`n要求值: v24.15.0`n检查方式: node -v`n修复: 安装 v24.15.0 后重试（不自动升级系统 Node.js）。" -f $nodeVersion)
}
if ($npmVersion -ne '11.16.0') {
    Write-Fail ("npm 版本不匹配。`n检测值: {0}`n要求值: 11.16.0`n检查方式: npm -v`n修复: 安装随 Node.js 24.15.0 附带的 npm 11.16.0 后重试。" -f $npmVersion)
}

$nodeModules = Join-Path $FrontendDir 'node_modules'
$needInstall = $ReinstallFrontendDependencies -or -not (Test-Path -LiteralPath $nodeModules)
if ($needInstall) {
    Write-Host '安装前端依赖 (npm ci，位于 frontend/) ...'
    Push-Location -LiteralPath $FrontendDir
    try {
        $npmCiLines = @(& npm ci 2>&1)
        if ($LASTEXITCODE -ne 0) {
            Write-Fail ("npm ci 失败，未启动前端。`n输出:`n{0}" -f (($npmCiLines | Out-String).Trim()))
        }
    } finally {
        Pop-Location
    }
} else {
    Write-Host 'frontend/node_modules 已存在，跳过 npm ci（需要重装时加 -ReinstallFrontendDependencies）。'
}

# ---- Step 6: port conflict check (report only; never kill, never switch ports) ----
foreach ($port in @(8011, 5174)) {
    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $ownerName = '?'
        $owner = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
        if ($owner) {
            $ownerName = $owner.ProcessName
        }
        Write-Fail ("端口 {0} 已被占用，不启动服务。`n占用进程: PID {1} ({2}), 监听地址 {3}`n处理: 不终止未知进程、不自动换端口。若这是本项目旧实例，请先在仓库根目录执行 .\stop.ps1；否则请自行释放该端口后重试。" -f $port, $listener.OwningProcess, $ownerName, $listener.LocalAddress)
    }
}

function Start-TrackedService {
    # Launch a service in its own PowerShell window and record identity for stop.ps1.
    param(
        [string]$Role,
        [string]$LauncherBody,
        [int[]]$Ports,
        [string]$CommandSummary
    )
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $random = [guid]::NewGuid().ToString('N').Substring(0, 8)
    $id = '{0}-{1}-{2}' -f $Role, $stamp, $random
    $launcherPath = Join-Path $RuntimeDir ($id + '.ps1')
    $recordPath = Join-Path $RuntimeDir ($id + '.json')
    $header = @(
        "`$host.UI.RawUI.WindowTitle = " + (ConvertTo-SingleQuotedLiteral ('BP-Reviewer ' + $Role + ' [' + $id + ']'))
        "`$ErrorActionPreference = 'Continue'"
    ) -join [Environment]::NewLine
    Set-Content -LiteralPath $launcherPath -Value ($header + [Environment]::NewLine + $LauncherBody) -Encoding UTF8
    try {
        $argumentList = '-NoProfile -ExecutionPolicy Bypass -NoExit -File "' + $launcherPath + '"'
        $process = Start-Process -FilePath powershell.exe -ArgumentList $argumentList -PassThru
        $record = [ordered]@{
            id        = $id
            role      = $Role
            pid       = $process.Id
            launcher  = $launcherPath
            command   = $CommandSummary
            startedAt = (Get-Date).ToString('o')
            ports     = @($Ports)
        }
        $record | ConvertTo-Json | Set-Content -LiteralPath $recordPath -Encoding UTF8
    } catch {
        Remove-Item -LiteralPath $launcherPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $recordPath -Force -ErrorAction SilentlyContinue
        throw
    }
    return $id
}

# ---- Steps 7-9: launch backend + frontend, verify readiness, print summary ----
$launchedIds = New-Object System.Collections.Generic.List[string]
try {
    # Step 7: start the backend
    $allowRootArgs = ''
    foreach ($root in $allowRoots) {
        $allowRootArgs += ' --allow-root ' + (ConvertTo-SingleQuotedLiteral $root)
    }
    # --no-capture-output keeps the access token streaming visibly in the backend window.
    $backendCommand = 'conda run --no-capture-output -n bpreviewer python -m bpreviewer.cli' + $allowRootArgs + ' --state ' + (ConvertTo-SingleQuotedLiteral $StateResolved) + ' --port 8011'
    $backendBody = @(
        'Set-Location -LiteralPath ' + (ConvertTo-SingleQuotedLiteral $RepoRoot)
        $backendCommand
        "Write-Host '后端已退出。可以关闭本窗口。'"
    ) -join [Environment]::NewLine
    Write-Host '启动后端 (独立终端窗口，本地访问令牌显示在该窗口中) ...'
    $backendId = Start-TrackedService -Role 'backend' -LauncherBody $backendBody -Ports @(8011) -CommandSummary $backendCommand
    $launchedIds.Add($backendId)

    # Step 8: start the frontend
    $frontendBody = @(
        'Set-Location -LiteralPath ' + (ConvertTo-SingleQuotedLiteral $FrontendDir)
        'npm run dev'
        "Write-Host '前端已退出。可以关闭本窗口。'"
    ) -join [Environment]::NewLine
    Write-Host '启动前端 (独立终端窗口，Vite 日志显示在该窗口中) ...'
    $frontendId = Start-TrackedService -Role 'frontend' -LauncherBody $frontendBody -Ports @(5174) -CommandSummary 'npm run dev (127.0.0.1:5174)'
    $launchedIds.Add($frontendId)

    # Step 9: readiness (token-protected API: use TCP for the backend, HTTP for the frontend)
    $backendUp = Wait-TcpListening -Address '127.0.0.1' -Port 8011 -TimeoutSeconds 30
    if (-not $backendUp) {
        Write-Host '后端启动超时，清理本脚本已启动的服务 ...'
        & (Join-Path $RepoRoot 'stop.ps1') -InstanceId @($launchedIds)
        Write-Fail "30 秒内 127.0.0.1:8011 未开始监听。请查看后端窗口（标题 BP-Reviewer backend [$backendId]）中的错误信息。已清理本脚本启动且可确认归属的进程。"
    }
    $frontendUp = Wait-HttpResponse -Url 'http://127.0.0.1:5174' -TimeoutSeconds 30
    if (-not $frontendUp) {
        Write-Host '前端启动超时，清理本脚本已启动的服务 ...'
        & (Join-Path $RepoRoot 'stop.ps1') -InstanceId @($launchedIds)
        Write-Fail "30 秒内 http://127.0.0.1:5174 未返回 HTTP 响应。请查看前端窗口（标题 BP-Reviewer frontend [$frontendId]）中的错误信息。已清理本脚本启动且可确认归属的进程，不将“浏览器已打开”视为启动成功。"
    }
} catch {
    if ($launchedIds.Count -gt 0) {
        Write-Host '启动过程中出现错误，清理本脚本已启动的服务 ...'
        & (Join-Path $RepoRoot 'stop.ps1') -InstanceId @($launchedIds)
    }
    Write-Fail ("启动过程中出现错误: {0}" -f $_.Exception.Message)
}

Write-Host ''
Write-Host '== BP-Reviewer 本地服务已启动 ==' -ForegroundColor Green
Write-Host '后端地址: http://127.0.0.1:8011'
Write-Host '前端地址: http://127.0.0.1:5174'
Write-Host ('扫描目标: ' + $TargetResolved)
Write-Host ('授权根目录: ' + ($allowRoots -join '; '))
Write-Host ('后端令牌: 显示在后端终端窗口（标题 BP-Reviewer backend [{0}]），在页面中输入该令牌' -f $backendId)
Write-Host ('前端日志: 前端终端窗口（标题 BP-Reviewer frontend [{0}]）' -f $frontendId)
Write-Host '停止服务: 在仓库根目录执行 .\stop.ps1，或在上述两个窗口分别按 Ctrl+C'
Write-Host ''

if (-not $NoBrowser) {
    Start-Process 'http://127.0.0.1:5174'
    Write-Host '已在默认浏览器打开 http://127.0.0.1:5174'
} else {
    Write-Host '已指定 -NoBrowser，不自动打开浏览器。'
}
exit 0
