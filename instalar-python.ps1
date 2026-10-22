#Requires -Version 5.1
<#
  Instala Python 3 no PERFIL DO USUARIO (SEM elevacao/admin) se ainda nao houver
  versao minima para o Kanban (Subir Kanban / compartilhado).

  - Ferramenta permanente: %LOCALAPPDATA%\Programs\Python (fora da pasta do projeto)
  - %TEMP% so para download do instalador; arquivo removido ao terminar
    (TEMP e limpo pelos usuarios — nao e lugar da ferramenta)
#>
param(
  # Chamado pelo Subir Kanban: se ja tem Python, sai 0 sem instalar; se falta, instala.
  [switch]$Auto
)

$ErrorActionPreference = "Stop"

# Minimo estavel para o projeto (stdlib + anotacoes modernas). Alvo de instalacao: 3.12.
$MinMajor = 3
$MinMinor = 9
$AlvoWinget = "Python.Python.3.12"
$AlvoUrl = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"

function Write-Info([string]$msg) { Write-Host $msg -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host $msg -ForegroundColor Green }
function Write-Warn([string]$msg) { Write-Host $msg -ForegroundColor Yellow }

function Get-PythonCandidates {
  $lista = New-Object System.Collections.Generic.List[string]
  # Preferir o python.exe real (nao o launcher / stub da Store)
  try {
    $out = & py -3 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) {
      $exe = ($out | Select-Object -First 1).ToString().Trim()
      if ($exe -and (Test-Path -LiteralPath $exe) -and ($exe -notmatch "WindowsApps\\")) {
        [void]$lista.Add($exe)
      }
    }
  } catch {}
  foreach ($cmd in @("python", "python3")) {
    try {
      $cmdInfo = Get-Command $cmd -ErrorAction SilentlyContinue
      if ($cmdInfo -and $cmdInfo.Source -and ($cmdInfo.Source -notmatch "WindowsApps\\")) {
        [void]$lista.Add($cmdInfo.Source)
      }
    } catch {}
  }
  $local = Join-Path $env:LOCALAPPDATA "Programs\Python"
  if (Test-Path -LiteralPath $local) {
    Get-ChildItem -LiteralPath $local -Filter "python.exe" -Recurse -ErrorAction SilentlyContinue |
      ForEach-Object { [void]$lista.Add($_.FullName) }
  }
  return $lista | Select-Object -Unique
}

function Test-PythonMinimo([string]$exe) {
  try {
    $code = "import sys; raise SystemExit(0 if sys.version_info >= ($MinMajor, $MinMinor) else 1)"
    & $exe -c $code 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

function Get-PythonVersao([string]$exe) {
  try {
    $v = & $exe -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
    if ($LASTEXITCODE -eq 0) { return ($v | Select-Object -First 1).ToString().Trim() }
  } catch {}
  return "?"
}

function Refresh-PathUser {
  $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $user = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = @($machine, $user) -join ";"
}

if (-not $Auto) {
  Write-Info "Kanban - preparacao do Python (sem elevacao / sem admin)"
  Write-Host "Pasta do projeto: $PSScriptRoot"
  Write-Host "Minimo: Python $MinMajor.$MinMinor+  |  Alvo: 3.12 no perfil do usuario"
  Write-Host ""
}

$ok = $null
foreach ($exe in Get-PythonCandidates) {
  if (Test-PythonMinimo $exe) {
    $ok = $exe
    break
  }
}

if ($ok) {
  if (-not $Auto) {
    Write-Ok ("Ja existe Python adequado: {0} ({1})" -f (Get-PythonVersao $ok), $ok)
    Write-Host "Pode usar o atalho Subir Kanban."
  }
  exit 0
}

Write-Warn "Python ainda nao esta pronto neste PC. Instalando no seu usuario (sem admin)..."
Write-Host ""

$instalou = $false

# 1) winget em escopo de usuario (opcional)
$winget = Get-Command winget -ErrorAction SilentlyContinue
if ($winget) {
  Write-Info "Tentando winget ($AlvoWinget, --scope user)..."
  try {
    & winget install --id $AlvoWinget -e --scope user --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq -1978335189) {
      # 0 = ok; -1978335189 = ja instalado
      $instalou = $true
    } else {
      Write-Warn ("winget nao concluiu (codigo {0}). Vou usar o instalador oficial." -f $LASTEXITCODE)
    }
  } catch {
    Write-Warn ("winget falhou: {0}. Vou usar o instalador oficial." -f $_.Exception.Message)
  }
} else {
  Write-Warn "winget nao encontrado neste PC. Sem problema: uso o instalador oficial do python.org."
}

# 2) Fallback: instalador oficial, so usuario atual; download em %TEMP% (apagado depois)
if (-not $instalou) {
  Write-Info "Baixando instalador oficial para %TEMP% (ferramenta vai para LocalAppData)..."
  $tmp = Join-Path $env:TEMP ("kanban-python-setup-{0}.exe" -f [guid]::NewGuid().ToString("N").Substring(0, 8))
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $AlvoUrl -OutFile $tmp -UseBasicParsing
    Write-Info "Instalando no perfil do usuario (InstallAllUsers=0)..."
    $args = @(
      "/quiet",
      "InstallAllUsers=0",
      "PrependPath=1",
      "Include_test=0",
      "Include_launcher=1",
      "SimpleInstall=1"
    )
    $p = Start-Process -FilePath $tmp -ArgumentList $args -Wait -PassThru -NoNewWindow
    if ($p.ExitCode -eq 0) {
      $instalou = $true
    } else {
      Write-Warn ("Instalador retornou codigo {0}" -f $p.ExitCode)
    }
  } catch {
    Write-Warn ("Falha no download/instalacao: {0}" -f $_.Exception.Message)
  } finally {
    if (Test-Path -LiteralPath $tmp) {
      Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
  }
}

Refresh-PathUser

$ok = $null
foreach ($exe in Get-PythonCandidates) {
  if (Test-PythonMinimo $exe) {
    $ok = $exe
    break
  }
}

Write-Host ""
if ($ok) {
  Write-Ok ("Pronto. Python {0}: {1}" -f (Get-PythonVersao $ok), $ok)
  Write-Host "Feche e abra o terminal se o comando py ainda nao aparecer, depois use Subir Kanban."
  exit 0
}

Write-Warn "Nao foi possivel instalar Python sem elevacao neste PC."
Write-Host "Opcoes: liberar winget/rede, ou instalar manualmente o Python 3.12+ em python.org marcando"
Write-Host "'Install for current user' e 'Add python.exe to PATH' (ainda sem admin)."
exit 1
