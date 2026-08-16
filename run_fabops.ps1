<#
.SYNOPSIS
    Install this checkout and start FabOps.

.DESCRIPTION
    A convenience wrapper around the two commands the README already documents:

        python -m pip install -e ".[app]"
        fabops-app

    It exists so that starting the product is one command instead of two, and
    so that the install always targets *this* checkout — the script locates the
    repository from its own path, which is why it works from any directory.

    It is a launcher and nothing else. It does not manage environments: it uses
    an activated virtual environment if there is one, the repository's `.venv`
    if there is not, and `python` from PATH otherwise. It does not reproduce the
    Streamlit command either — `fabops-app` owns that (src/fabapp/cli.py), and a
    launcher that guessed at it would be a second thing to keep in step.

.EXAMPLE
    .\run_fabops.ps1
#>

# The repository root is the directory this script lives in. `$PSScriptRoot` is
# resolved by PowerShell rather than from the caller's location, so the install
# below is of this checkout no matter where the launcher was invoked from.
$repositoryRoot = $PSScriptRoot

Write-Host "========================================"
Write-Host " FabOps Launcher"
Write-Host "========================================"
Write-Host ""

# Environment selection, in three lines and no more: an activated virtual
# environment wins because activating one is a statement of intent; the
# repository's own `.venv` is next because a checkout that has one is already
# set up; `python` from PATH is the fallback and is what the README assumes.
if ($env:VIRTUAL_ENV -and (Test-Path (Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"))) {
    $python = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
} elseif (Test-Path (Join-Path $repositoryRoot ".venv\Scripts\python.exe")) {
    $python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
} else {
    $python = "python"
}

Write-Host "Using python: $python"
Write-Host ""
Write-Host "Installing FabOps..."
Write-Host ""

# `-e ".[app]"` is relative to the current directory, so run it from the root.
Push-Location $repositoryRoot
try {
    & $python -m pip install -e ".[app]"
    $installExitCode = $LASTEXITCODE
} catch {
    Write-Host ""
    Write-Host $_.Exception.Message
    $installExitCode = 1
} finally {
    Pop-Location
}

if ($installExitCode -ne 0) {
    Write-Host ""
    Write-Host "FabOps installation failed."
    Write-Host "FabOps was not started."
    exit $installExitCode
}

Write-Host ""
Write-Host "Installation successful."
Write-Host ""

# The console script belongs to the interpreter that installed it, and that
# interpreter's Scripts directory is not necessarily on PATH — so look there
# first, then on PATH for the `python`-from-PATH case.
$fabopsApp = $null
if ($python -ne "python") {
    $candidate = Join-Path (Split-Path -Parent $python) "fabops-app.exe"
    if (Test-Path $candidate) { $fabopsApp = $candidate }
}
if (-not $fabopsApp) {
    $onPath = Get-Command "fabops-app" -ErrorAction SilentlyContinue
    if ($onPath) { $fabopsApp = $onPath.Source }
}

if (-not $fabopsApp) {
    Write-Host "Installation reported success, but the 'fabops-app' command could not be found."
    Write-Host "FabOps was not started."
    exit 1
}

Write-Host "Starting FabOps..."
Write-Host ""

# The terminal stays attached to the application: its output is this window's
# output, Ctrl+C reaches it, and its exit code is the launcher's exit code.
& $fabopsApp
exit $LASTEXITCODE
