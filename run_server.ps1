# PowerShell script to start llama-server for sfr_gen
# Commit this to Git — it documents exactly how to start the server.
# The .gguf file and .exe are NOT in Git (see .gitignore).

param(
    [int]$Port      = 8080,
    [int]$CtxSize   = 4096,
    [int]$GpuLayers = 0        # 0 = CPU only; e.g. 35 to offload layers to GPU
)

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServerExe  = Join-Path $ScriptDir "llm_runtime\llama-server.exe"
$CfgFile    = Join-Path $ScriptDir "llm_runtime\model.cfg"

# ── Resolve model path from config or glob ──────────────────────────────────
if (Test-Path $CfgFile) {
    $cfg       = Get-Content $CfgFile | ConvertFrom-Json
    $ModelPath = $cfg.model_path
    $Port      = if ($cfg.server_port) { $cfg.server_port } else { $Port }
} else {
    # Fall back to first .gguf in llm_runtime/
    $gguf = Get-ChildItem (Join-Path $ScriptDir "llm_runtime\*.gguf") -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $gguf) {
        Write-Error "No GGUF model found. Run: python setup/download_model.py"
        exit 1
    }
    $ModelPath = $gguf.FullName
}

# ── Validate files ───────────────────────────────────────────────────────────
if (-not (Test-Path $ServerExe)) {
    Write-Error "llama-server.exe not found at $ServerExe`nRun: python setup/download_model.py"
    exit 1
}
if (-not (Test-Path $ModelPath)) {
    Write-Error "Model not found at $ModelPath`nRun: python setup/download_model.py"
    exit 1
}

# ── Check if already running ──────────────────────────────────────────────────
$testConn = $null
try {
    $testConn = New-Object System.Net.Sockets.TcpClient("127.0.0.1", $Port)
} catch {}

if ($testConn -and $testConn.Connected) {
    $testConn.Close()
    Write-Host "[server] Port $Port already in use — server is already running." -ForegroundColor Yellow
    Write-Host "[server] Endpoint: http://127.0.0.1:$Port/v1" -ForegroundColor Cyan
    exit 0
}

# ── Start the server ──────────────────────────────────────────────────────────
$ModelName = Split-Path -Leaf $ModelPath
Write-Host ""
Write-Host "Starting llama-server …" -ForegroundColor Green
Write-Host "  Model : $ModelName"
Write-Host "  Port  : $Port"
Write-Host "  GPU layers: $GpuLayers (0 = CPU only)"
Write-Host ""

$args = @(
    "--model",        $ModelPath,
    "--port",         $Port,
    "--host",         "127.0.0.1",
    "--ctx-size",     $CtxSize,
    "--n-gpu-layers", $GpuLayers
)

& $ServerExe @args
