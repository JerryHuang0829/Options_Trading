# Windows Defender 排除清單 — Options_Trading repo (R11.4 P1 修法)
#
# 解 Codex env / 並行測試時 AV real-time scan 鎖檔造成的 PermissionError WinError 5.
# 排除路徑只縮 AV 對 pytest / cache 暫存區的掃描，不影響 src/ data/ 等真實資料.
#
# 使用：右鍵以管理員執行；或 PowerShell 管理員視窗跑：
#   .\scripts\setup_windows_defender_exclusions.ps1

[CmdletBinding()]
param(
    [string]$RepoRoot = "E:\Data\chongweihuang\Desktop\project\Options_Trading"
)

$ErrorActionPreference = "Stop"
$logPath = Join-Path $RepoRoot "scripts\setup_av_exclusions_log.txt"

# 確認管理員權限
$id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object System.Security.Principal.WindowsPrincipal($id)
if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    "NOT_ADMIN: 請以系統管理員身分重新執行 PowerShell" | Out-File $logPath -Encoding utf8
    Write-Host "錯誤：需要系統管理員權限" -ForegroundColor Red
    Pause
    exit 1
}

# 排除路徑（只縮 pytest 暫存區，不縮真實資料）
$paths = @(
    (Join-Path $RepoRoot "tests\_tmp"),
    (Join-Path $RepoRoot ".pytest_cache"),
    (Join-Path $RepoRoot ".pytest_tmp")
)

$results = @()
foreach ($p in $paths) {
    try {
        Add-MpPreference -ExclusionPath $p -ErrorAction Stop
        $results += "OK: $p"
    } catch {
        $results += "FAIL: $p — $($_.Exception.Message)"
    }
}

# 列已排除路徑
$current = (Get-MpPreference).ExclusionPath
$results += ""
$results += "=== Get-MpPreference ExclusionPath ==="
foreach ($cp in $current) { $results += "  $cp" }

$results | Out-File $logPath -Encoding utf8
$results | ForEach-Object { Write-Host $_ }
Write-Host ""
Write-Host "結果已寫入: $logPath" -ForegroundColor Green
Pause
