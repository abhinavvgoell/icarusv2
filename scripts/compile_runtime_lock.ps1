$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonImage = 'python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f'
$mount = "type=bind,source=$projectRoot,target=/src"

docker run --rm --mount $mount -w /src $pythonImage sh -c "python -m pip install --disable-pip-version-check --root-user-action=ignore pip-tools==7.6.2 && pip-compile --generate-hashes --strip-extras --output-file=requirements-runtime.lock requirements-runtime.in"
if ($LASTEXITCODE -ne 0) { throw "Runtime lock compilation failed with exit code $LASTEXITCODE" }
