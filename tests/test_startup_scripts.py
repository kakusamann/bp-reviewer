"""Checks for the Windows one-click scripts (start.ps1 / stop.ps1 / start.bat).

Static consistency and security checks run everywhere. Scenario tests invoke the
real scripts with stub tools on the PATH and are skipped off Windows. The full
launch/stop integration test is opt-in via BPREVIEWER_LAUNCH_TESTS=1 because it
binds the real 8011/5174 ports and starts the real conda/Node toolchain.
"""

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
START_PS1 = REPO / "start.ps1"
STOP_PS1 = REPO / "stop.ps1"
START_BAT = REPO / "start.bat"
FRONTEND = REPO / "frontend"
STATE_RUNTIME = REPO / "state" / "runtime"

IS_WINDOWS = sys.platform == "win32"
POWERSHELL = shutil.which("powershell") or shutil.which("powershell.exe")
RUN_LAUNCH = os.environ.get("BPREVIEWER_LAUNCH_TESTS") == "1"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def fingerprints(root: Path) -> dict:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def run_powershell(
    script: Path, args: list[str], env_overrides: dict | None = None, timeout: int = 120
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_overrides:
        for key, value in env_overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    parts = ["'" + str(script).replace("'", "''") + "'"]
    for arg in args:
        text = str(arg)
        if text.startswith("-"):
            parts.append(text)  # parameter names must stay unquoted for binding
        else:
            parts.append("'" + text.replace("'", "''") + "'")
    # -Command lets us force UTF-8 stdout so Chinese diagnostics survive capture on any locale.
    inner = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; & " + " ".join(parts)
    command = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", inner]
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout
    )


def write_stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="ascii", errors="strict")


def stub_env(directory: Path, extra_path: str | None = None) -> dict:
    path_entries = [str(directory)]
    if extra_path:
        path_entries.append(extra_path)
    path_entries.append(os.environ["PATH"])
    return {"PATH": ";".join(path_entries)}


# ---- Static checks (run on every platform) ----


def test_required_script_files_exist():
    for path in (START_PS1, STOP_PS1, START_BAT):
        assert path.is_file(), f"missing deliverable: {path}"


def test_start_bat_is_thin_wrapper():
    content = read(START_BAT)
    assert "start.ps1" in content
    assert "%~dp0" in content
    for forbidden in ("conda", "npm ", "8011", "5174", "bpreviewer.cli"):
        assert forbidden not in content, f"start.bat must not implement business logic: {forbidden}"


def test_powershell_scripts_have_utf8_bom_for_windows_powershell():
    for path in (START_PS1, STOP_PS1):
        assert path.read_bytes().startswith(
            b"\xef\xbb\xbf"
        ), f"{path.name} must keep a UTF-8 BOM for Windows PowerShell 5.1"


def test_powershell_scripts_parse_without_errors():
    shells = [s for s in (POWERSHELL, shutil.which("pwsh")) if s]
    if not shells:
        pytest.skip("no PowerShell parser available on this machine")
    for shell in shells:
        for script in (START_PS1, STOP_PS1):
            probe = (
                "$errs = $null; [System.Management.Automation.Language.Parser]::ParseFile("
                f"'{script}', [ref]$null, [ref]$errs) | Out-Null; "
                "if ($errs.Count -gt 0) { $errs | ForEach-Object { Write-Output $_.ToString() }; exit 1 }"
            )
            result = subprocess.run(
                [shell, "-NoProfile", "-Command", probe], capture_output=True, text=True, timeout=120
            )
            assert result.returncode == 0, f"{shell} parse errors in {script.name}:\n{result.stdout}\n{result.stderr}"


def test_ports_agree_with_frontend_config():
    package_json = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))
    dev = package_json["scripts"]["dev"]
    assert "127.0.0.1" in dev and "5174" in dev and "--strictPort" in dev
    assert package_json["engines"] == {"node": "24.15.0", "npm": "11.16.0"}
    vite_config = (FRONTEND / "vite.config.ts").read_text(encoding="utf-8")
    assert "http://127.0.0.1:8011" in vite_config
    start = read(START_PS1)
    assert "8011" in start and "5174" in start and "127.0.0.1" in start
    assert (FRONTEND / ".nvmrc").read_text(encoding="utf-8").strip() == "24.15.0"


def test_backend_cli_arguments_agree_with_scripts():
    cli_source = (REPO / "backend" / "src" / "bpreviewer" / "cli.py").read_text(encoding="utf-8")
    for flag in ("--allow-root", "--state", "--port", "8011", "127.0.0.1", "BPREVIEWER_TOKEN"):
        assert flag in cli_source, f"cli.py drifted from the documented interface: {flag}"
    start = read(START_PS1)
    for flag in ("--allow-root", "--state", "--port", "8011", "python -m bpreviewer.cli", "conda run"):
        assert flag in start, f"start.ps1 must pass the real CLI interface: {flag}"
    for line in start.splitlines():
        if "conda run" in line:
            assert "-n bpreviewer" in line, f"start.ps1 must only use the bpreviewer conda environment: {line}"


def test_only_loopback_binding_and_no_public_exposure():
    for script in (START_PS1, STOP_PS1):
        content = read(script)
        assert "0.0.0.0" not in content
        for tunnel in ("ngrok", "cloudflared", "localtunnel", "serveo"):
            assert tunnel not in content.lower()
        for url in re.findall(r"https?://[^\s'\"]+", content):
            host = url.split("//", 1)[1].split("/", 1)[0].split(":")[0]
            assert host in {"127.0.0.1", "localhost"}, f"non-loopback URL in {script.name}: {url}"


def test_no_hardcoded_token_or_destructive_commands():
    for script in (START_PS1, STOP_PS1):
        content = read(script)
        assert re.search(r"(?i)\btoken\b\s*=\s*['\"][^'\"]+['\"]", content) is None, "no token literals in scripts"
        for bad in ("git pull", "git checkout", "git reset", "git clean", "git clone"):
            assert bad not in content, f"scripts must not run {bad}"
        assert (
            re.search(r"&\s+[^\n]*\bpip\s+install", content) is None
        ), "scripts must never install packages themselves"
        assert (
            re.search(r"&\s+conda\s+(install|update|create\b|env\s+create)", content) is None
        ), "scripts must never modify conda environments"
        assert "Invoke-Expression" not in content and "iex " not in content.lower()


def test_writes_stay_out_of_scan_targets():
    start = read(START_PS1)
    write_line = re.compile(r"(Set-Content|Out-File|Add-Content|New-Item|Remove-Item)")
    for line in start.splitlines():
        if write_line.search(line):
            assert not re.search(
                r"TargetResolved|allowRoots|ProjectPath", line
            ), f"write cmdlet touching a scan target: {line}"


def test_default_and_project_path_error_messages_cover_requirements():
    start = read(START_PS1)
    assert "默认目标目录不存在" in start
    assert "-ProjectPath" in start
    assert "conda env create -f environment.yml" in start
    assert "pip install" in start  # fix hint only; execution is forbidden elsewhere


# ---- Scenario tests: real scripts + stub tools (Windows only) ----

scenario_only = pytest.mark.skipif(not (IS_WINDOWS and POWERSHELL), reason="needs Windows PowerShell")


def base_stubs(
    directory: Path, node_version: str = "v24.15.0", npm_version: str = "11.16.0", conda_envs: str | None = None
) -> dict:
    if conda_envs is None:
        conda_envs = r'{"envs": ["C:\\fake\\envs\\bpreviewer"]}'
    write_stub(
        directory,
        "conda.cmd",
        "@echo off\r\n"
        'if "%1"=="env" if "%2"=="list" (\r\n'
        f"  echo {conda_envs}\r\n"
        "  exit /b 0\r\n"
        ")\r\n"
        'if "%1"=="run" exit /b 0\r\n'
        "exit /b 0\r\n",
    )
    write_stub(directory, "node.cmd", f"@echo off\r\necho {node_version}\r\n")
    write_stub(
        directory,
        "npm.cmd",
        f"@echo off\r\n"
        f'if "%1"=="-v" (echo {npm_version} & exit /b 0)\r\n'
        f'if "%1"=="ci" exit /b 0\r\n'
        f"exit /b 0\r\n",
    )
    return stub_env(directory)


@scenario_only
def test_missing_project_path_fails_with_fix(tmp_path):
    missing = tmp_path / "no-such-project"
    result = run_powershell(START_PS1, ["-ProjectPath", str(missing)])
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert str(missing) in combined
    assert "-ProjectPath" in combined


@scenario_only
def test_state_inside_target_is_refused(tmp_path):
    result = run_powershell(START_PS1, ["-ProjectPath", str(REPO)])
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "状态目录不得位于扫描目标内部" in combined


@scenario_only
def test_missing_conda_reports_install_command(tmp_path):
    env = {"PATH": str(tmp_path / "empty") + ";" + os.environ.get("SystemRoot", r"C:\Windows") + r"\System32"}
    (tmp_path / "empty").mkdir()
    result = run_powershell(
        START_PS1, ["-ProjectPath", str(REPO / "examples" / "tiny_pipeline"), "-NoBrowser"], env_overrides=env
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "conda" in combined.lower()


@scenario_only
def test_missing_bpreviewer_env_reports_create_command(tmp_path):
    stubs = tmp_path / "stubs"
    env = base_stubs(stubs, conda_envs=r'{"envs": ["C:\\fake\\envs\\other"]}')
    result = run_powershell(
        START_PS1, ["-ProjectPath", str(REPO / "examples" / "tiny_pipeline"), "-NoBrowser"], env_overrides=env
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "conda env create -f environment.yml" in combined


@scenario_only
def test_node_version_mismatch_reports_detected_and_required(tmp_path):
    stubs = tmp_path / "stubs"
    env = base_stubs(stubs, node_version="v20.0.0")
    result = run_powershell(
        START_PS1, ["-ProjectPath", str(REPO / "examples" / "tiny_pipeline"), "-NoBrowser"], env_overrides=env
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "v20.0.0" in combined and "v24.15.0" in combined
    assert "node -v" in combined


@scenario_only
def test_port_conflict_reports_owner_without_killing(tmp_path):
    stubs = tmp_path / "stubs"
    env = base_stubs(stubs)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", 8011))
        listener.listen(1)
    except OSError:
        listener.close()
        pytest.skip("port 8011 already occupied on this machine")
    try:
        result = run_powershell(
            START_PS1, ["-ProjectPath", str(REPO / "examples" / "tiny_pipeline"), "-NoBrowser"], env_overrides=env
        )
        combined = result.stdout + result.stderr
        assert result.returncode != 0
        assert "8011" in combined
        assert str(os.getpid()) in combined
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            assert probe.connect_ex(("127.0.0.1", 8011)) == 0, "occupied port must stay untouched (no killing)"
    finally:
        listener.close()


@scenario_only
def test_npm_ci_failure_blocks_frontend_launch(tmp_path):
    stubs = tmp_path / "stubs"
    env = base_stubs(stubs)
    (stubs / "npm.cmd").write_text(
        '@echo off\r\nif "%1"=="-v" (echo 11.16.0 & exit /b 0)\r\necho simulated npm ci failure 1>&2\r\nexit /b 1\r\n',
        encoding="ascii",
    )
    before = set(p.name for p in STATE_RUNTIME.glob("*")) if STATE_RUNTIME.is_dir() else set()
    result = run_powershell(
        START_PS1,
        ["-ProjectPath", str(REPO / "examples" / "tiny_pipeline"), "-NoBrowser", "-ReinstallFrontendDependencies"],
        env_overrides=env,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "npm ci" in combined
    after = set(p.name for p in STATE_RUNTIME.glob("*")) if STATE_RUNTIME.is_dir() else set()
    assert after == before, "failed start must not leave launch records behind"


# ---- Full launch/stop integration (opt-in: binds real ports) ----


def api_request(base: str, token: str, method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(base + path, data=body, method=method)
    request.add_header("Authorization", "Bearer " + token)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


@pytest.mark.skipif(not RUN_LAUNCH, reason="set BPREVIEWER_LAUNCH_TESTS=1 to run real launch/stop")
def test_full_launch_scan_and_stop(tmp_path):
    for port in (8011, 5174):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                pytest.skip(f"port {port} already in use; stop the other service before the integration test")
    token = "test-" + uuid.uuid4().hex
    env_overrides = {"BPREVIEWER_TOKEN": token}
    target = tmp_path / "synthetic"
    target.mkdir()
    (target / "unit.py").write_text(
        "def source(x):\n    return x\n\ndef sink(x):\n    return source(x)\n", encoding="utf-8"
    )
    before = fingerprints(target)
    args = ["-ProjectPath", str(target), "-NoBrowser"]
    started = False
    snapshot_id = None
    try:
        result = run_powershell(START_PS1, args, env_overrides=env_overrides)
        combined = result.stdout + result.stderr
        assert result.returncode == 0, combined
        assert "已指定 -NoBrowser" in combined
        started = True
        with socket.create_connection(("127.0.0.1", 8011), timeout=5):
            pass
        with urllib.request.urlopen("http://127.0.0.1:5174", timeout=10) as page:
            assert page.status == 200
        projects = api_request("http://127.0.0.1:5174/api", token, "GET", "/projects")
        allowed = [Path(p).resolve() for p in projects["allowed_roots"]]
        assert target.resolve() in allowed
        assert (REPO / "examples" / "tiny_pipeline").resolve() in allowed
        assert not any(Path(p).resolve() == REPO.parent.resolve() for p in allowed)
        snapshot = api_request("http://127.0.0.1:5174/api", token, "POST", "/projects/scan", {"path": str(target)})
        snapshot_id = snapshot["id"]
        assert fingerprints(target) == before, "scanning must not modify the target"
    finally:
        if started:
            stop = run_powershell(STOP_PS1, [])
            combined = stop.stdout + stop.stderr
            assert stop.returncode == 0, combined
    assert fingerprints(target) == before
    time.sleep(0.5)
    for port in (8011, 5174):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0, f"port {port} still listening after stop.ps1"
    leftovers = [p for p in STATE_RUNTIME.glob("*.json")] if STATE_RUNTIME.is_dir() else []
    assert leftovers == [], "stop.ps1 must clean launch records"
    assert (REPO / "state" / "snapshots" / (snapshot_id + ".json")).is_file(), "snapshots must survive stop.ps1"
