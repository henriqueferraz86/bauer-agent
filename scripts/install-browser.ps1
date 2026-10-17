$ErrorActionPreference = 'Stop'

$python = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

& $python -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "Playwright não conseguiu instalar o Chromium (exit code $LASTEXITCODE)."
}

Write-Host 'Chromium compatível com o Playwright instalado.'
