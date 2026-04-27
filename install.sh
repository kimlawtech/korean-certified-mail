#!/bin/bash
# korean-certified-mail 설치 스크립트
# 사용법: bash install.sh

set -e

SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "설치 경로: $SKILLS_DIR"
echo "소스 경로: $REPO_DIR"
echo ""

if [ ! -d "$SKILLS_DIR" ]; then
  echo "오류: $SKILLS_DIR 디렉토리가 없습니다."
  echo "Claude Code가 설치되어 있는지 확인하세요."
  exit 1
fi

SKILL="korean-certified-mail"
SKILL_MD="$REPO_DIR/certified-mail/SKILL.md"

# {REPO_DIR} 플레이스홀더를 실제 경로로 치환
if [ -f "$SKILL_MD" ]; then
  sed -i '' "s|{REPO_DIR}|$REPO_DIR|g" "$SKILL_MD"
  echo "경로 치환 완료: $SKILL_MD"
fi

# 심링크 생성
TARGET="$SKILLS_DIR/$SKILL"
SOURCE="$REPO_DIR/certified-mail"

if [ -L "$TARGET" ]; then
  rm "$TARGET"
elif [ -d "$TARGET" ]; then
  echo "경고: $TARGET 이 일반 디렉토리로 존재합니다. 건너뜁니다."
else
  :
fi

ln -s "$SOURCE" "$TARGET"
echo "스킬 심링크 생성: $TARGET → $SOURCE"

# MCP 서버 설정
echo ""
echo "MCP 서버 설정 중..."

CLAUDE_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
MCP_SERVER_PATH="$REPO_DIR/mcp-server/server.py"

# Python 의존성 설치
echo "Python 의존성 설치 중..."
if command -v pip3 &>/dev/null; then
  pip3 install -q mcp 2>/dev/null || echo "pip3 설치 실패 — 수동으로 'pip3 install mcp' 실행하세요."
else
  echo "pip3 없음 — Python 3가 설치되어 있는지 확인하세요."
fi

# claude_desktop_config.json에 MCP 서버 등록
if [ -f "$CLAUDE_CONFIG" ]; then
  # jq 사용 가능 시 안전하게 추가
  if command -v python3 &>/dev/null; then
    python3 - <<PYEOF
import json, sys
config_path = "$CLAUDE_CONFIG"
server_path = "$MCP_SERVER_PATH"

with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

if 'mcpServers' not in config:
    config['mcpServers'] = {}

if 'korean-certified-mail' not in config['mcpServers']:
    config['mcpServers']['korean-certified-mail'] = {
        "command": "python3",
        "args": [server_path]
    }
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print("MCP 서버 등록 완료: korean-certified-mail")
else:
    print("MCP 서버 이미 등록됨: korean-certified-mail")
PYEOF
  else
    echo "Python3 없음 — MCP 서버를 수동으로 등록하세요."
    echo "등록 경로: $CLAUDE_CONFIG"
    echo '  "korean-certified-mail": { "command": "python3", "args": ["'"$MCP_SERVER_PATH"'"] }'
  fi
else
  echo "claude_desktop_config.json을 찾을 수 없습니다."
  echo "Claude Desktop이 설치되어 있는지 확인하세요."
fi

echo ""
echo "설치 완료."
echo ""
echo "다음 단계:"
echo "  1. Claude Desktop을 완전히 종료 후 재실행"
echo "  2. Claude Code에서 /certified-mail 입력"
