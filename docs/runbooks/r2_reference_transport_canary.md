# Isolated R2 reference-transport canary

This runbook is only for the deterministic R2 transport canary. It does not
authorize a live run. `boto3`, `botocore`, the R2 bucket, and R2 credentials are
optional canary-only resources; normal Tella operation does not require them.

## Authorization boundary

Do not proceed unless a reviewer has separately approved the exact branch,
commit, bucket, token scope, operation budget, private-bucket confirmation, and
`IfNoneMatch="*"` behavior test. Never run this procedure against production
objects or user media.

The harness must remain R2-only. It must not call BFL, Workers AI, Gemini, TTS,
ASR, stock, music, or rendering services.

## Isolated Python environment

The project declares `r2-canary` as an optional extra. Install it only into the
dedicated ignored environment, together with the development tools needed for
local validation:

```powershell
$isolatedEnvironment = Join-Path $PWD ".venv-r2-canary"
$env:UV_PROJECT_ENVIRONMENT = $isolatedEnvironment
try {
    uv sync --locked --extra dev --extra r2-canary
}
finally {
    Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
}
```

Normal commands without `--extra r2-canary` must not install boto3, botocore,
s3transfer, or jmespath.

To remove the isolated environment, first close every process using it and
verify its exact resolved path remains directly below the current repository:

```powershell
$repositoryRoot = (Resolve-Path -LiteralPath $PWD).Path
$isolatedEnvironment = [IO.Path]::GetFullPath(
    (Join-Path $repositoryRoot ".venv-r2-canary")
)
$expectedEnvironment = [IO.Path]::GetFullPath(
    (Join-Path $repositoryRoot ".venv-r2-canary")
)

if ($isolatedEnvironment -ne $expectedEnvironment) {
    throw "Refusing to remove an unexpected environment path."
}

if (Test-Path -LiteralPath $isolatedEnvironment) {
    $item = Get-Item -LiteralPath $isolatedEnvironment -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to remove a symlink or junction."
    }
    Remove-Item -LiteralPath $isolatedEnvironment -Recurse
}
```

## Dedicated private bucket

Create one bucket solely for reference-transport canaries. The operator must
confirm all of the following:

- Public access is disabled.
- `r2.dev` public access is disabled.
- No public custom domain is attached.
- The bucket contains no production objects.
- The bucket contains no user photographs or reference media.
- Canary objects use only the `reference-sheets/` prefix.
- The shortest appropriate lifecycle deletion policy supported by R2 is active.
- Immediate executor cleanup remains mandatory; lifecycle deletion is backup.
- A second operator independently verified the bucket is private.

Do not reuse a production bucket and do not record its real name in Git, chat,
logs, screenshots, or review reports.

## Restricted token policy

Create a dedicated S3-compatible R2 credential scoped only to the selected
canary bucket. Permit only the object operations required by the executor:

- upload the temporary deterministic object;
- inspect object existence and owner metadata;
- retrieve the exact object bytes;
- create the locally signed temporary read request;
- list only when bounded reconciliation requires it;
- delete the owned temporary object.

Reject account-wide administrative tokens, global Cloudflare API keys,
browser-session credentials, Workers AI tokens, BFL credentials, credentials
for production buckets, and tokens covering unrelated R2 buckets.

When the R2 token UI cannot restrict an object prefix, the dedicated bucket is
the hard security boundary and the harness-enforced `reference-sheets/` prefix
is the application boundary.

Never provide secret values through chat, Git, command arguments, `.env`, logs,
screenshots, transcripts, or reports.

## Process-scoped secret injection

Use a new private PowerShell process. Disable persistent command history and do
not enable transcription, shell logging, screen sharing, or debug tracing.

```powershell
Set-PSReadLineOption -HistorySaveStyle SaveNothing

function Set-ProcessSecretFromPrompt {
    param([Parameter(Mandatory)][string]$Name)

    $secure = Read-Host "Enter $Name" -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        [Environment]::SetEnvironmentVariable($Name, $plain, "Process")
    }
    finally {
        $plain = $null
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $secure.Dispose()
    }
}

Set-ProcessSecretFromPrompt R2_ACCOUNT_ID
Set-ProcessSecretFromPrompt R2_ACCESS_KEY_ID
Set-ProcessSecretFromPrompt R2_SECRET_ACCESS_KEY
Set-ProcessSecretFromPrompt R2_BUCKET_NAME
```

Verify presence only. Never print, hash, measure, compare, or partially reveal
the values:

```powershell
$r2Names = @(
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME"
)

$r2Names | ForEach-Object {
    [pscustomobject]@{
        Name = $_
        Present = [bool][Environment]::GetEnvironmentVariable($_, "Process")
    }
}
```

All four booleans must be `True` before a separately authorized run.

Clear the process variables immediately afterward, confirm all booleans are
`False`, clear in-memory history, and close the shell:

```powershell
$r2Names | ForEach-Object {
    [Environment]::SetEnvironmentVariable($_, $null, "Process")
}

$r2Names | ForEach-Object {
    [pscustomobject]@{
        Name = $_
        Present = [bool][Environment]::GetEnvironmentVariable($_, "Process")
    }
}

Clear-History
```

## Temporary confirmation configuration

The committed canary configuration is deliberately fail-closed. Only after the
independent privacy and conditional-write reviews pass, create an untracked
temporary configuration outside the repository:

```powershell
$baseConfig = Join-Path $PWD "configs/benchmarks/r2_reference_transport_canary_v1.json"
$liveConfig = Join-Path $env:TEMP "tella-r2-reference-transport-canary-confirmed.json"

if (Test-Path -LiteralPath $liveConfig) {
    throw "Refusing to overwrite an existing temporary canary configuration."
}

$config = Get-Content -Raw -LiteralPath $baseConfig | ConvertFrom-Json
$config.transport_policy.private_bucket_status_confirmed = $true
$config.transport_policy.conditional_write_test_confirmed = $true
$json = $config | ConvertTo-Json -Depth 20
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
```

## Future live command

The following exact command is documentation only. Do not execute it without a
new explicit authorization checkpoint:

```powershell
$canaryPython = Join-Path $PWD ".venv-r2-canary/Scripts/python.exe"
$liveConfigCreated = $false

try {
    [System.IO.File]::WriteAllText(
        $liveConfig,
        $json,
        $utf8WithoutBom
    )
    $liveConfigCreated = $true

    $configBytes = [System.IO.File]::ReadAllBytes($liveConfig)
    if (
        $configBytes.Length -ge 3 -and
        $configBytes[0] -eq 0xEF -and
        $configBytes[1] -eq 0xBB -and
        $configBytes[2] -eq 0xBF
    ) {
        throw "Refusing a temporary canary configuration with a UTF-8 BOM."
    }

    & $canaryPython -m scripts.benchmarks.r2_reference_transport_canary `
        --config $liveConfig `
        --mode live-r2 `
        --authorization-token AUTHORIZE_R2_REFERENCE_TRANSPORT_CANARY_01
    if ($LASTEXITCODE -ne 0) {
        throw "The R2 reference-transport canary failed."
    }
}
finally {
    if ($liveConfigCreated -and (Test-Path -LiteralPath $liveConfig)) {
        Remove-Item -LiteralPath $liveConfig
    }
}
```

The command does not require `BFL_API_KEY` and cannot construct BFL.

## Reviewed real-canary observation

The separately authorized R2-only transport canary completed with these
redacted observations:

- Status: passed.
- Source and roundtrip SHA256 matched
  `99ac29d0e49ebcb6a8ed06859beb8d6d59c1c926198c2d66b1a940ac97db2ceb`.
- The payload was a 414-byte, 64x64 `image/png`.
- The identical conditional write returned 412.
- The conflicting conditional write returned 412.
- The borrowed-object policy was verified.
- Cleanup deleted the owned object.
- Post-cleanup absence was confirmed.
- `cleanup_required` was false.

No endpoint, hostname, account identifier, signed URL, credential, production
image, user photo, or character reference is part of this record.

## Fixed operation budget

- R2 client constructions: maximum 1.
- Immutable writes: maximum 3 (initial, identical conditional, conflicting
  conditional).
- SDK total attempts per operation: 1.
- Presign operations: maximum 1.
- Verification downloads: maximum 1.
- Cleanup attempts: maximum 2.
- Automatic retries: 0.
- Fallbacks: 0.
- BFL, Workers AI, Gemini, and render calls: 0.

The deterministic 64x64 PNG SHA256 must be:

`99ac29d0e49ebcb6a8ed06859beb8d6d59c1c926198c2d66b1a940ac97db2ceb`

Cleanup is required on normal success, upload ambiguity, presign failure,
verification-download failure, hash/MIME/decode failure, conditional-write
failure, and cancellation. Delete only an object proven to be owned by the
current invocation.

## Mandatory pre-live checklist

- [ ] The reviewed branch and commit are exact.
- [ ] The worktree is clean and staged changes are zero.
- [ ] The optional SDK versions and lockfile were reviewed.
- [ ] The `.venv-r2-canary` environment is isolated and ignored.
- [ ] The bucket is dedicated and independently verified private.
- [ ] Public access, `r2.dev`, and custom domains are disabled.
- [ ] The token is restricted to the selected canary bucket.
- [ ] All four process variables report `Present=True` without displaying values.
- [ ] `BFL_API_KEY` is not required.
- [ ] The deterministic PNG hash matches exactly.
- [ ] The fixed operation budget is unchanged.
- [ ] Immediate cleanup and lifecycle cleanup are understood.
- [ ] No production object, user photo, or reference media is involved.
- [ ] `IfNoneMatch="*"` behavior testing has explicit approval.
- [ ] The exact live authorization token has separate approval.

Stop on any unchecked item. Environment preparation alone never authorizes the
live canary.
