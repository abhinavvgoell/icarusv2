$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonImage = 'python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f'
$mount = "type=bind,source=$projectRoot,target=/src,readonly"

docker run --rm --mount $mount -w /src $pythonImage sh -c "python -m pip install --disable-pip-version-check --root-user-action=ignore pip-audit==2.10.1 && pip-audit -r requirements-runtime.lock"
if ($LASTEXITCODE -ne 0) { throw "Runtime dependency audit failed with exit code $LASTEXITCODE" }
