$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Storage = Join-Path $Root "storage"

Write-Host "============================================================"
Write-Host "ARMORED CREATOR - RESET LAB DE CERTIFICACAO"
Write-Host "============================================================"
Write-Host "Root: $Root"
Write-Host ""
Write-Host "ATENCAO: isto apaga SOMENTE estado de laboratorio:"
Write-Host "  - SQLite/database"
Write-Host "  - storage/videos"
Write-Host "  - storage/logs"
Write-Host "  - filas/pastas legadas conhecidas"
Write-Host "NAO apaga credentials, session Telegram, assets ou codigo."
Write-Host ""

$answer = Read-Host "Digite RESET ARMORED para confirmar"
if ($answer -ne "RESET ARMORED") {
    Write-Host "Reset cancelado."
    exit 1
}

$targets = @(
    (Join-Path $Storage "database"),
    (Join-Path $Storage "videos"),
    (Join-Path $Storage "logs"),
    (Join-Path $Storage "sync"),
    (Join-Path $Storage "sync video"),
    (Join-Path $Storage "queue"),
    (Join-Path $Storage "publish_queue"),
    (Join-Path $Storage "pipeline"),
    (Join-Path $Storage "hub"),
    (Join-Path $Storage "generated"),
    (Join-Path $Storage "rejected"),
    (Join-Path $Storage "archive"),
    (Join-Path $Storage "input"),
    (Join-Path $Storage "output"),
    (Join-Path $Storage "temp")
)

foreach ($target in $targets) {
    if (Test-Path -LiteralPath $target) {
        Write-Host "Removendo: $target"
        Remove-Item -LiteralPath $target -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $Storage "database"), (Join-Path $Storage "videos"), (Join-Path $Storage "logs"), (Join-Path $Storage "backups") | Out-Null

Write-Host ""
Write-Host "RESET CONCLUIDO."
Write-Host "Credenciais/sessao Telegram e assets foram preservados."