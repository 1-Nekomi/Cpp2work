param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ImportArgs)
$ErrorActionPreference = 'Stop'
$runtimeCandidates = @(
    $env:JUDGE_PYTHON,
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
)
$taskPython = $null
foreach ($candidate in $runtimeCandidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { $taskPython = $candidate; break }
}
if (-not $taskPython) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $taskPython = $command.Source }
}
if (-not $taskPython) { throw 'Python 3.10+ is required. Set JUDGE_PYTHON to its executable path.' }
$env:PYTHONIOENCODING = 'utf-8'
Push-Location $PSScriptRoot
try {
    & $taskPython (Join-Path $PSScriptRoot 'import_homework.py') @ImportArgs
    $taskExitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $taskExitCode
