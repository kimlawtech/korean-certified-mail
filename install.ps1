# korean-certified-mail 설치 스크립트 (Windows PowerShell)
# 사용법: powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"

$SkillsDir = if ($env:CLAUDE_SKILLS_DIR) { $env:CLAUDE_SKILLS_DIR } else { "$env:USERPROFILE\.claude\skills" }
$RepoDir   = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "설치 경로: $SkillsDir"
Write-Host "소스 경로: $RepoDir"

if (-not (Test-Path $SkillsDir)) {
    Write-Error "오류: $SkillsDir 디렉토리가 없습니다. Claude Code가 설치되어 있는지 확인하세요."
    exit 1
}

$Skill   = "korean-certified-mail"
$SkillMd = Join-Path $RepoDir "certified-mail\SKILL.md"

# {REPO_DIR} 치환
if (Test-Path $SkillMd) {
    $content = Get-Content $SkillMd -Raw -Encoding UTF8
    $content = $content -replace '\{REPO_DIR\}', $RepoDir.Replace('\', '/')
    Set-Content $SkillMd -Value $content -Encoding UTF8
    Write-Host "경로 치환 완료: $SkillMd"
}

# 심링크 생성
$Target = Join-Path $SkillsDir $Skill
$Source = Join-Path $RepoDir "certified-mail"

if (Test-Path $Target) {
    Remove-Item $Target -Force -Recurse
}
New-Item -ItemType Junction -Path $Target -Target $Source | Out-Null
Write-Host "스킬 심링크 생성: $Target → $Source"

# MCP 서버 설정
$ClaudeConfig = "$env:APPDATA\Claude\claude_desktop_config.json"
$ServerPath   = Join-Path $RepoDir "mcp-server\server.py"

if (Test-Path $ClaudeConfig) {
    $config = Get-Content $ClaudeConfig -Raw | ConvertFrom-Json
    if (-not $config.mcpServers) {
        $config | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value @{}
    }
    if (-not $config.mcpServers."korean-certified-mail") {
        $config.mcpServers | Add-Member -MemberType NoteProperty -Name "korean-certified-mail" -Value @{
            command = "python3"
            args    = @($ServerPath.Replace('\', '/'))
        }
        $config | ConvertTo-Json -Depth 10 | Set-Content $ClaudeConfig -Encoding UTF8
        Write-Host "MCP 서버 등록 완료: korean-certified-mail"
    } else {
        Write-Host "MCP 서버 이미 등록됨: korean-certified-mail"
    }
} else {
    Write-Host "claude_desktop_config.json을 찾을 수 없습니다. 수동으로 등록하세요."
}

Write-Host ""
Write-Host "설치 완료. Claude Desktop을 재실행하세요."
