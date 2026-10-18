$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$workspace = Join-Path $root 'imports\validation-final'

# Prefer validation-final when it has data; otherwise use the newest imported
# batch anywhere under the project (for example a custom output folder chosen
# in the Import form). If nothing exists, keep the default path and let the
# workbench start with an empty workspace so the Import menu can create a batch.
if (-not (Test-Path -LiteralPath (Join-Path $workspace 'batch.json'))) {
    $found = Get-ChildItem -LiteralPath $root -Directory -Recurse -Depth 2 -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notin @('.git', '__pycache__') -and (Test-Path -LiteralPath (Join-Path $_.FullName 'batch.json')) } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($found) { $workspace = $found.FullName }
}

& (Join-Path $root 'import.ps1') review --workspace $workspace --open
