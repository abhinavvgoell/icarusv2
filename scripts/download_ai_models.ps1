$ErrorActionPreference = 'Stop'

$modelRoot = Join-Path $PSScriptRoot '..\data\models\icarus-ai'
$files = @(
    @{ Path='qa\model_int8.onnx'; Size=65567726; Sha256='e572df4912fdc231f377192bb7632c662c2326f1799a5dfc2049c33e4be2f4d5'; Url='https://huggingface.co/Xenova/distilbert-base-cased-distilled-squad/resolve/bdbb0a5e9c617c3c7ea9f3cbaf2be3ad871bcd7d/onnx/model_int8.onnx' },
    @{ Path='qa\tokenizer.json'; Size=668923; Sha256='343989712a36cd8b253efeaf8baf6a08b9d2583f78e395e83832e8ee9f8d8ee1'; Url='https://huggingface.co/Xenova/distilbert-base-cased-distilled-squad/resolve/bdbb0a5e9c617c3c7ea9f3cbaf2be3ad871bcd7d/tokenizer.json' },
    @{ Path='classify\model_O4.onnx'; Size=45212349; Sha256='1667d7f3ba669048b13a96ee3a44456d5e42c8f44588ae8b603430e16160c485'; Url='https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/onnx/model_O4.onnx' },
    @{ Path='classify\tokenizer.json'; Size=466247; Sha256='be50c3628f2bf5bb5e3a7f17b1f74611b2561a3a27eeab05e5aa30f411572037'; Url='https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/tokenizer.json' }
)

foreach ($item in $files) {
    $target = Join-Path $modelRoot $item.Path
    $directory = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    if (Test-Path -LiteralPath $target) {
        $existing = Get-Item -LiteralPath $target
        $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -eq $item.Sha256) {
            Write-Host "Verified existing $($item.Path)"
            continue
        }
        if ($existing.Length -ge $item.Size) {
            Remove-Item -LiteralPath $target
        }
    }
    Write-Host "Downloading $($item.Path) (about $([math]::Ceiling($item.Size / 1MB)) MB)"
    & curl.exe --location --fail --retry 5 --retry-all-errors --retry-delay 2 --continue-at - --output $target $item.Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed for $($item.Path)" }
    $downloaded = Get-Item -LiteralPath $target
    $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($downloaded.Length -ne $item.Size -or $hash -ne $item.Sha256) {
        throw "Integrity check failed for $($item.Path): size=$($downloaded.Length), sha256=$hash"
    }
    Write-Host "Verified $($item.Path)"
}

Write-Host "AI assets are installed under $modelRoot"
