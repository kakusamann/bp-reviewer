# BP-Reviewer local stop. Only stops services launched by start.ps1 whose process
# identity (recorded PID + command line containing the per-instance launcher path)
# is verified. Never kills by port, never touches unrelated Python/Node/Vite
# processes, and keeps state/ snapshots, drafts and change requests intact.

[CmdletBinding()]
param(
    # Optional instance IDs to stop; default is every recorded instance.
    [string[]]$InstanceId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($env:OS -ne 'Windows_NT') {
    Write-Host 'ERROR: stop.ps1 仅支持 Windows。' -ForegroundColor Red
    exit 1
}

$RepoRoot = $PSScriptRoot
$RuntimeDir = Join-Path (Join-Path $RepoRoot 'state') 'runtime'

$exitCode = 0
$stopped = 0
$leftovers = 0

if (-not (Test-Path -LiteralPath $RuntimeDir)) {
    Write-Host '没有已记录的启动实例（state/runtime 不存在），无需停止。'
    exit 0
}

$records = @(Get-ChildItem -LiteralPath $RuntimeDir -Filter '*.json' -ErrorAction SilentlyContinue)
if ($InstanceId) {
    $records = @($records | Where-Object { $InstanceId -contains $_.BaseName })
}
if (-not $records) {
    Write-Host '没有匹配的启动实例记录，无需停止。'
    exit 0
}

# Snapshot the process table once; classify descendants of each recorded wrapper.
$processTable = @{}
foreach ($proc in @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, CommandLine, Name)) {
    $processTable[[int]$proc.ProcessId] = $proc
}

function Get-Descendants {
    # Depth-first descendants of $RootPid, deepest last (children before their parents).
    param([int]$RootPid)
    $chain = New-Object System.Collections.Generic.List[object]
    $queue = New-Object System.Collections.Generic.Queue[int]
    $queue.Enqueue($RootPid)
    $seen = @{ $RootPid = $true }
    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        foreach ($candidate in $processTable.Values) {
            $childPid = [int]$candidate.ProcessId
            if ($seen.ContainsKey($childPid)) {
                continue
            }
            if ([int]$candidate.ParentProcessId -eq $current) {
                $seen[$childPid] = $true
                $chain.Add($candidate)
                $queue.Enqueue($childPid)
            }
        }
    }
    $reversed = New-Object System.Collections.Generic.List[object]
    for ($i = $chain.Count - 1; $i -ge 0; $i--) {
        $reversed.Add($chain[$i])
    }
    return $reversed
}

foreach ($recordFile in $records) {
    $data = $null
    $id = '?'
    $procId = -1
    $launcherPath = ''
    $role = ''
    try {
        $data = Get-Content -LiteralPath $recordFile.FullName -Raw | ConvertFrom-Json
        $id = [string]$data.id
        $procId = [int]$data.pid
        $launcherPath = [string]$data.launcher
        $role = [string]$data.role
        if ($procId -lt 0 -or -not $launcherPath) {
            throw '记录缺少 pid 或 launcher 字段'
        }
    } catch {
        Write-Host ("记录文件无法解析或字段缺失: {0}（不终止任何进程，请人工确认后删除该文件）" -f $recordFile.FullName) -ForegroundColor Yellow
        $leftovers++
        continue
    }

    $process = $null
    if ($processTable.ContainsKey($procId)) {
        $process = $processTable[$procId]
    }
    if ($null -eq $process) {
        Write-Host ("[{0}] PID {1} 已退出，清理启动记录。" -f $id, $procId)
        Remove-Item -LiteralPath $recordFile.FullName -Force
        if ($launcherPath -and (Test-Path -LiteralPath $launcherPath)) {
            Remove-Item -LiteralPath $launcherPath -Force
        }
        continue
    }

    $commandLine = [string]$process.CommandLine
    if (-not $commandLine) {
        Write-Host ("[{0}] 无法读取 PID {1} 的命令行，不能确认归属；不终止该进程。" -f $id, $procId) -ForegroundColor Yellow
        Write-Host ("    请在对应终端按 Ctrl+C 停止服务，然后手动删除记录: {0}" -f $recordFile.FullName) -ForegroundColor Yellow
        $leftovers++
        $exitCode = 1
        continue
    }
    if (-not $commandLine.Contains($launcherPath)) {
        # PID no longer belongs to this instance (reused by the system); never kill it.
        Write-Host ("[{0}] PID {1} 的命令行与启动记录不符（可能已被回收复用），不终止该进程，清理过期记录。" -f $id, $procId) -ForegroundColor Yellow
        Remove-Item -LiteralPath $recordFile.FullName -Force
        if ($launcherPath -and (Test-Path -LiteralPath $launcherPath)) {
            Remove-Item -LiteralPath $launcherPath -Force
        }
        continue
    }

    # Ownership verified: stop matching service processes below the wrapper, then the wrapper.
    $servicePattern = if ($role -eq 'backend') { 'bpreviewer\.cli' } else { 'vite|npm-cli|run\s+dev' }
    $killed = 0
    foreach ($descendant in Get-Descendants -RootPid $procId) {
        $descCommandLine = [string]$descendant.CommandLine
        if ($descCommandLine -and ($descCommandLine -match $servicePattern)) {
            try {
                Stop-Process -Id ([int]$descendant.ProcessId) -Force -ErrorAction Stop
                $killed++
            } catch {
                Write-Host ("[{0}] 无法结束进程 PID {1}: {2}" -f $id, $descendant.ProcessId, $_.Exception.Message) -ForegroundColor Yellow
            }
        } elseif ($descCommandLine) {
            Write-Host ("[{0}] 跳过启动窗口内非本服务进程: PID {1} ({2})" -f $id, $descendant.ProcessId, $descendant.Name) -ForegroundColor Yellow
        }
    }
    try {
        Stop-Process -Id $procId -Force -ErrorAction Stop
        $killed++
    } catch {
        Write-Host ("[{0}] 无法结束启动窗口 PID {1}: {2}" -f $id, $procId, $_.Exception.Message) -ForegroundColor Yellow
    }
    if ($killed -gt 0) {
        Write-Host ("[{0}] 已停止 {1} 个进程（含启动窗口 PID {2}）。" -f $id, $killed, $procId)
        $stopped++
    } else {
        Write-Host ("[{0}] 服务已退出，仅清理启动记录（保留启动窗口以便查看输出）。" -f $id)
    }
    Remove-Item -LiteralPath $recordFile.FullName -Force
    if ($launcherPath -and (Test-Path -LiteralPath $launcherPath)) {
        Remove-Item -LiteralPath $launcherPath -Force
    }
}

Write-Host ''
if ($leftovers -gt 0) {
    Write-Host ("仍有 {0} 个实例无法确认归属，未强制终止；请在对应终端按 Ctrl+C 停止。" -f $leftovers) -ForegroundColor Yellow
}
Write-Host ("完成：停止 {0} 个实例。state/ 中的扫描快照、草稿和变更需求已保留。" -f $stopped)
exit $exitCode
