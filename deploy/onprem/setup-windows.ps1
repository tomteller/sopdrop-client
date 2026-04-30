#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Sets up a Sopdrop on-prem server on Windows Server (2022+) using WSL2.

.DESCRIPTION
    End-to-end installer for self-hosting Sopdrop on a Windows box. Wraps the
    same docker-compose stack that runs on Linux — uses WSL2 + Ubuntu 22.04
    underneath, so all existing operational tooling (compose, pg_dump
    backups, schema migrations) keeps working unchanged.

    The script is idempotent and resumable:
      1st run (fresh box): enables WSL features, installs Ubuntu, asks you
                           to reboot, then exits.
      You reboot, complete the Ubuntu first-run prompt (set a Linux user +
      password), then re-run the script.
      2nd run: installs Docker inside Ubuntu, clones the public client repo,
               writes deploy/onprem/.env (generates secrets), runs the
               compose stack, opens port 4800 on the Windows firewall, and
               adds the WSL portproxy.

    Re-running after that is safe — every step checks state first.

.PARAMETER PublicUrl
    The URL artists hit from Houdini, e.g. http://sopdrop.lan:4800. If you
    don't have internal DNS yet, use the server's LAN IP. Required on the
    second run; you'll be prompted if not supplied.

.PARAMETER Port
    Host port the API is published on. Defaults to 4800.

.PARAMETER Distro
    WSL distro name. Defaults to Ubuntu-22.04.

.PARAMETER PublicRepo
    Public client repo URL. Defaults to the canonical one.

.EXAMPLE
    # First run (fresh box):
    .\setup-windows.ps1
    # ...reboot, finish Ubuntu first-run...

    # Second run:
    .\setup-windows.ps1 -PublicUrl http://192.168.1.50:4800
#>

[CmdletBinding()]
param(
    [string]$PublicUrl,
    [int]   $Port       = 4800,
    [string]$Distro     = "Ubuntu-22.04",
    [string]$PublicRepo = "https://github.com/tomteller/sopdrop-client.git"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    $msg" -ForegroundColor Green }
function Write-Skip($msg)  { Write-Host "    $msg" -ForegroundColor DarkGray }
function Write-Warn2($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

function Test-WslFeatureEnabled {
    $wsl = (Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux).State
    $vmp = (Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform).State
    return ($wsl -eq "Enabled") -and ($vmp -eq "Enabled")
}

function Test-DistroInstalled([string]$Name) {
    # `wsl -l -q` lists installed distros, one per line. Names contain trailing
    # null bytes on some Windows builds — strip and trim.
    $list = (wsl.exe -l -q) 2>$null
    if (-not $list) { return $false }
    return ($list | ForEach-Object { ($_ -replace "`0","").Trim() }) -contains $Name
}

function Invoke-Wsl([string]$Script, [switch]$AsRoot) {
    # Run a bash script inside the WSL distro. -e so any failure aborts.
    $args = @("-d", $Distro)
    if ($AsRoot) { $args += @("-u", "root") }
    $args += @("--", "bash", "-e")
    $Script | & wsl.exe @args
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed (exit $LASTEXITCODE)"
    }
}

function Get-WslDefaultUser {
    # The user the distro logs in as by default (set during first-run).
    (wsl.exe -d $Distro -- bash -lc 'echo $USER').Trim()
}

# ────────────────────────────────────────────────────────────────────────────
# Phase 1: WSL2 features + Ubuntu install
# ────────────────────────────────────────────────────────────────────────────

Write-Step "Checking WSL2 prerequisites"

if (-not (Test-WslFeatureEnabled)) {
    Write-Warn2 "Enabling WSL + VirtualMachinePlatform features (requires reboot)"
    dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart | Out-Null
    dism.exe /online /enable-feature /featurename:VirtualMachinePlatform           /all /norestart | Out-Null

    Write-Host ""
    Write-Host "REBOOT REQUIRED." -ForegroundColor Yellow
    Write-Host "After reboot:"
    Write-Host "  1. Open a Start menu entry for '$Distro' (it will appear once installed)."
    Write-Host "  2. If not present, run: wsl --install -d $Distro"
    Write-Host "  3. Set a Linux username + password when prompted."
    Write-Host "  4. Re-run this script."
    exit 0
}
Write-Ok "WSL2 features enabled"

# wsl --set-default-version 2 is idempotent. Cheap to re-run.
wsl.exe --set-default-version 2 | Out-Null

if (-not (Test-DistroInstalled $Distro)) {
    Write-Warn2 "Installing $Distro (this opens an interactive first-run window)"
    wsl.exe --install -d $Distro
    Write-Host ""
    Write-Host "Complete the $Distro first-run window:" -ForegroundColor Yellow
    Write-Host "  - Set a UNIX username + password."
    Write-Host "  - Wait for the prompt to finish, then close the window."
    Write-Host "  - Re-run this script to continue."
    exit 0
}
Write-Ok "$Distro installed"

# ────────────────────────────────────────────────────────────────────────────
# Phase 2: Docker inside WSL
# ────────────────────────────────────────────────────────────────────────────

Write-Step "Checking Docker inside $Distro"

$dockerPresent = $false
try {
    wsl.exe -d $Distro -- bash -lc 'command -v docker >/dev/null 2>&1' 2>$null
    if ($LASTEXITCODE -eq 0) { $dockerPresent = $true }
} catch {}

if (-not $dockerPresent) {
    Write-Warn2 "Installing Docker Engine inside $Distro"
    Invoke-Wsl -AsRoot @'
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git
install -m 0755 -d /etc/apt/keyrings
if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
fi
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
'@
    Write-Ok "Docker installed"
} else {
    Write-Skip "Docker already installed"
}

# Add the default WSL user to the docker group + start the daemon on shell open.
$wslUser = Get-WslDefaultUser
Write-Step "Configuring Docker for user '$wslUser'"
Invoke-Wsl -AsRoot @"
usermod -aG docker $wslUser
# Auto-start dockerd when this user opens a WSL shell. Idempotent.
RC=/home/$wslUser/.bashrc
LINE='if ! pgrep -x dockerd >/dev/null 2>&1; then sudo service docker start >/dev/null 2>&1; fi'
grep -qF "`$LINE" "`$RC" || echo "`$LINE" >> "`$RC"
# Passwordless sudo for `service docker start` only — narrow grant.
echo '$wslUser ALL=(root) NOPASSWD: /usr/sbin/service docker start' > /etc/sudoers.d/sopdrop-docker-start
chmod 0440 /etc/sudoers.d/sopdrop-docker-start
service docker start >/dev/null 2>&1 || true
"@
Write-Ok "Docker daemon running"

# ────────────────────────────────────────────────────────────────────────────
# Phase 3: Clone repo + write .env
# ────────────────────────────────────────────────────────────────────────────

$repoPath = "/home/$wslUser/sopdrop"
$onpremPath = "$repoPath/deploy/onprem"

Write-Step "Fetching sopdrop public repo"
Invoke-Wsl @"
if [ -d "$repoPath/.git" ]; then
    cd "$repoPath" && git pull --ff-only
else
    git clone "$PublicRepo" "$repoPath"
fi
"@
Write-Ok "Repo at $repoPath"

# .env: generate once, never overwrite. If it exists we keep the secrets the
# user already has — re-running this script must not rotate them silently.
Write-Step "Configuring .env"
$envExists = $false
try {
    wsl.exe -d $Distro -- bash -lc "test -f $onpremPath/.env" 2>$null
    if ($LASTEXITCODE -eq 0) { $envExists = $true }
} catch {}

if ($envExists) {
    Write-Skip ".env already present (leaving as-is — edit it manually if you need to change anything)"
} else {
    if (-not $PublicUrl) {
        Write-Host ""
        Write-Host "Need the URL artists will use from Houdini." -ForegroundColor Yellow
        Write-Host "Use a hostname your LAN resolves (preferred) or this server's LAN IP."
        $PublicUrl = Read-Host "PUBLIC_URL (e.g. http://192.168.1.50:$Port)"
    }
    if (-not $PublicUrl) { throw "PublicUrl is required" }

    Invoke-Wsl @"
PUB='$PublicUrl'
PORT='$Port'
PG_PASS=`$(openssl rand -hex 24)
JWT=`$(openssl rand -hex 32)
cat > "$onpremPath/.env" <<EOF
POSTGRES_DB=sopdrop
POSTGRES_USER=sopdrop
POSTGRES_PASSWORD=`$PG_PASS

JWT_SECRET=`$JWT

PUBLIC_URL=`$PUB
WEB_URL=`$PUB
CORS_ORIGINS=`$PUB

BIND_HOST=0.0.0.0
BIND_PORT=`$PORT

SKIP_EMAIL_VERIFICATION=true
BETA_MODE=false
TRUST_LAN_AUTH=true

GOOGLE_CLIENT_ID=
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
DISCORD_REDIRECT_URI=
EOF
chmod 600 "$onpremPath/.env"
"@
    Write-Ok "Wrote $onpremPath/.env (secrets generated)"
}

# ────────────────────────────────────────────────────────────────────────────
# Phase 4: Bring up the stack
# ────────────────────────────────────────────────────────────────────────────

Write-Step "Starting compose stack"
Invoke-Wsl "cd $onpremPath && docker compose up -d --build"

# Wait for health. The internal healthcheck has start_period=20s, so we
# poll for up to 60s.
Write-Step "Waiting for /api/health"
$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        wsl.exe -d $Distro -- bash -lc "curl -sf http://localhost:$Port/api/health >/dev/null" 2>$null
        if ($LASTEXITCODE -eq 0) { $healthy = $true; break }
    } catch {}
}
if ($healthy) {
    Write-Ok "Server is healthy on localhost:$Port (inside WSL)"
} else {
    Write-Warn2 "Health check did not return OK after 60s — check 'docker compose logs sopdrop-server'"
}

# ────────────────────────────────────────────────────────────────────────────
# Phase 5: Forward port to LAN + firewall rule
# ────────────────────────────────────────────────────────────────────────────

Write-Step "Configuring Windows portproxy + firewall for port $Port"

# Remove any stale entry first so we don't double-add.
& netsh interface portproxy delete v4tov4 listenport=$Port listenaddress=0.0.0.0 2>$null | Out-Null
& netsh interface portproxy add    v4tov4 listenport=$Port listenaddress=0.0.0.0 connectport=$Port connectaddress=127.0.0.1 | Out-Null
Write-Ok "portproxy 0.0.0.0:$Port → 127.0.0.1:$Port"

$ruleName = "Sopdrop $Port"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if (-not $existing) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -LocalPort $Port -Protocol TCP -Action Allow -Profile Domain,Private | Out-Null
    Write-Ok "firewall rule '$ruleName' created (Domain + Private profiles only)"
} else {
    Write-Skip "firewall rule '$ruleName' already exists"
}

# ────────────────────────────────────────────────────────────────────────────
# Done
# ────────────────────────────────────────────────────────────────────────────

$lanIp = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias Ethernet* -ErrorAction SilentlyContinue |
          Where-Object { $_.PrefixOrigin -ne "WellKnown" } |
          Select-Object -First 1 -ExpandProperty IPAddress)

Write-Host ""
Write-Host "Sopdrop on-prem server is up." -ForegroundColor Green
Write-Host ""
Write-Host "Workstations point Houdini → Sopdrop panel → Settings at:"
if ($lanIp) {
    Write-Host "    http://${lanIp}:${Port}" -ForegroundColor Cyan
} else {
    Write-Host "    http://<this-server-lan-ip>:${Port}" -ForegroundColor Cyan
}
Write-Host ""
Write-Host "Useful commands (run inside WSL: 'wsl -d $Distro'):"
Write-Host "  cd $onpremPath"
Write-Host "  docker compose ps                # service status"
Write-Host "  docker compose logs -f           # tail logs"
Write-Host "  docker compose down              # stop stack"
Write-Host "  docker compose up -d --build     # apply updates after 'git pull'"
Write-Host ""
Write-Host "Backups land at: \\wsl$\$Distro\home\$wslUser\sopdrop\deploy\onprem\backups"
