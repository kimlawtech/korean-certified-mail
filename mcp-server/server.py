#!/usr/bin/env python3
"""
korean-certified-mail MCP Server

보안 레이어 3종:
  Layer 1 — 입력 sanitize: 인젝션 패턴 차단 + 제어문자 제거
  Layer 2 — 개인정보 마스킹: Claude 컨텍스트에 PII 미전달
  Layer 3 — 파일 검증: load_contract_for_review 간접 인젝션 방어
"""

import re
import uuid
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

# ── 로거 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("certified-mail-mcp")

# ── 경로 설정 ─────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent.parent
OUTPUT_DIR = Path.home() / "Desktop"
DOCX_GEN   = BASE_DIR / "shared" / "certified-mail-docx.py"

# ── 세션 저장소 ───────────────────────────────────────────
_sessions: dict[str, dict] = {}
SESSION_TTL_MINUTES = 60  # 1시간 미사용 세션 자동 만료

mcp = FastMCP("korean-certified-mail")


# ══════════════════════════════════════════════════════════
# LAYER 1 — 입력 Sanitize
# ══════════════════════════════════════════════════════════

# 알려진 프롬프트 인젝션 패턴 (한국어 + 영어)
_INJECTION_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    # 영어 지시 무력화
    r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?|context)",
    r"disregard\s+(all\s+)?(previous|prior|instructions?|rules?)",
    r"forget\s+(everything|all|previous|prior|your\s+instructions?)",
    r"you\s+are\s+now\s+(?!a\s+lawyer|an?\s+attorney)",  # "You are now DAN" 류
    r"(act|behave)\s+as\s+(if\s+you\s+are\s+)?(a\s+)?(different|new|evil|unethical)",
    r"(jailbreak|dan|developer\s+mode|sudo\s+mode|god\s+mode)",
    r"(reveal|show|print|output|display|leak)\s+(your\s+)?(system\s+)?(prompt|instruction|rule|config)",
    r"(pretend|roleplay|simulate)\s+(that\s+)?(you\s+)?(have\s+no|without)\s+(restriction|filter|rule|limit)",
    r"new\s+(instruction|directive|command|override)",
    # 한국어 지시 무력화
    r"(이전|앞의|위의|기존)\s*(지시|명령|규칙|프롬프트|설정|내용)[\s을를은는이가]*\s*(무시|잊어|따르지\s*마|삭제)",
    r"(지금부터|이제부터|앞으로는)\s*(다른|새로운|모든)\s*(역할|페르소나|지시|명령)",
    r"(프롬프트|시스템\s*프롬프트|지시문|설정)\s*(출력|보여|공개|유출|알려)",
    r"(제한|필터|규칙|제약)\s*(없이|무시|해제|우회)",
    r"(비밀|숨겨진|내부)\s*(정보|프롬프트|명령|지시)\s*(알려|공개|출력)",
    # 역할 탈취
    r"(당신은|너는|you\s+are)\s*(이제|now)\s*(해커|악당|사기꾼|범죄자|無制限)",
    # 구분자 탈출 시도
    r"[-]{3,}[\s\S]{0,20}(system|instruction|prompt)",
    r"<\s*(system|instruction|prompt|rule)\s*>",
    r"\[\s*(system|instruction|new_prompt|override)\s*\]",
    # 간접 인젝션 — 데이터 필드 내 코드/명령
    r"```[\s\S]{0,50}(exec|eval|import|subprocess|os\.system)",
]]

# 파일 검증 상수
_MAX_FILE_BYTES  = 200 * 1024   # 200KB
_MAX_LINE_LEN    = 600           # 줄당 최대 600자
_MAX_LINE_COUNT  = 2000          # 최대 2000줄

# 허용 output_dir 루트 (Path Traversal 방지)
_ALLOWED_ROOT_PARENTS = [
    Path.home(),
    Path("/tmp"),
]


def _sanitize_str(value: str, field_name: str = "") -> tuple[str, bool]:
    """
    문자열 입력을 정제한다.
    반환: (정제된 값, 인젝션 감지 여부)
    """
    if not isinstance(value, str):
        return str(value), False

    # 제어문자 제거 (null byte, BS, DEL 등)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)

    # 유니코드 방향 제어 문자 (RLO/LRO — 텍스트 위장 공격)
    cleaned = re.sub(r"[​-‏‪-‮⁦-⁩﻿]", "", cleaned)

    # 인젝션 패턴 검사
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            log.warning("injection_detected field=%s snippet=%.60r", field_name, cleaned)
            return "[BLOCKED]", True

    return cleaned, False


def _sanitize_dict(data: dict) -> tuple[dict, list[str]]:
    """
    dict 전체를 재귀 sanitize.
    반환: (정제된 dict, 차단된 필드 목록)
    """
    cleaned: dict = {}
    blocked: list[str] = []

    for key, value in data.items():
        if isinstance(value, str):
            v, injected = _sanitize_str(value, field_name=key)
            cleaned[key] = v
            if injected:
                blocked.append(key)
        elif isinstance(value, dict):
            v, sub_blocked = _sanitize_dict(value)
            cleaned[key] = v
            blocked.extend(sub_blocked)
        elif isinstance(value, list):
            cleaned[key] = [
                _sanitize_str(item, field_name=key)[0] if isinstance(item, str) else item
                for item in value
            ]
        else:
            cleaned[key] = value

    return cleaned, blocked


def _sanitize_file_content(content: str) -> tuple[str, list[str]]:
    """
    파일 내용을 줄 단위로 검사한다.
    반환: (정제된 내용, 경고 메시지 목록)
    """
    warnings: list[str] = []
    lines = content.split("\n")

    if len(lines) > _MAX_LINE_COUNT:
        warnings.append(f"줄 수 초과: {len(lines)}줄 → {_MAX_LINE_COUNT}줄로 자름")
        lines = lines[:_MAX_LINE_COUNT]

    cleaned_lines: list[str] = []
    for i, line in enumerate(lines, start=1):
        # 비정상적으로 긴 줄 (숨겨진 인젝션 의심)
        if len(line) > _MAX_LINE_LEN:
            warnings.append(f"line {i}: 길이 초과({len(line)}자) → 자름")
            line = line[:_MAX_LINE_LEN] + "[...]"

        v, injected = _sanitize_str(line, field_name=f"file:line{i}")
        if injected:
            warnings.append(f"line {i}: 인젝션 패턴 감지 → 차단")
            v = "[BLOCKED LINE]"

        cleaned_lines.append(v)

    return "\n".join(cleaned_lines), warnings


def _validate_output_dir(path_str: str) -> tuple[Path, str | None]:
    """
    output_dir 경로가 허용된 루트 아래에 있는지 검증한다 (Path Traversal 방지).
    반환: (검증된 Path, 에러 메시지 or None)
    """
    if not path_str:
        return OUTPUT_DIR, None

    try:
        p = Path(path_str).expanduser().resolve()
    except Exception:
        return OUTPUT_DIR, f"경로 파싱 실패: {path_str!r} → 기본 경로 사용"

    for allowed in _ALLOWED_ROOT_PARENTS:
        try:
            p.relative_to(allowed.resolve())
            return p, None
        except ValueError:
            continue

    log.warning("path_traversal_attempt path=%s", path_str)
    return OUTPUT_DIR, f"허용되지 않은 경로: {path_str!r} → 기본 경로(Desktop) 사용"


def _expire_sessions() -> None:
    """TTL 초과 세션 정리."""
    cutoff = datetime.now() - timedelta(minutes=SESSION_TTL_MINUTES)
    expired = [
        sid for sid, s in _sessions.items()
        if datetime.fromisoformat(s["created_at"]) < cutoff
    ]
    for sid in expired:
        del _sessions[sid]
    if expired:
        log.info("session_expired count=%d", len(expired))


# ══════════════════════════════════════════════════════════
# TOOL 1 — 세션 목록 확인
# ══════════════════════════════════════════════════════════

@mcp.tool()
def list_sessions() -> dict:
    """활성 세션 목록 반환. MCP 서버 연결 상태 확인에 사용."""
    _expire_sessions()
    return {
        "status": "connected",
        "active_sessions": len(_sessions),
        "session_ids": list(_sessions.keys()),
    }


# ══════════════════════════════════════════════════════════
# TOOL 2 — 개인정보 마스킹  (Layer 1 + Layer 2)
# ══════════════════════════════════════════════════════════

@mcp.tool()
def mask_personal_info(mail_data: dict) -> dict:
    """
    내용증명 입력 데이터에서 개인정보를 마스킹 토큰으로 치환한다.

    보안 처리:
      - 모든 문자열 필드에 인젝션 패턴 검사 적용
      - PII 필드는 토큰으로 치환해 Claude 컨텍스트에서 격리
      - 금액은 백만 단위 힌트(AMOUNT_3M)만 전달
    """
    _expire_sessions()

    # Layer 1: 입력 전체 sanitize
    mail_data, blocked_fields = _sanitize_dict(mail_data)
    if blocked_fields:
        log.warning("mask_personal_info blocked_fields=%s", blocked_fields)

    session_id = uuid.uuid4().hex[:12]
    masked: dict = {}
    mapping: dict = {}

    MASK_RULES = {
        "senderName":        ("SENDER_NAME",  "name"),
        "senderAddress":     ("SENDER_ADDR",  "address"),
        "senderContact":     ("SENDER_TEL",   "contact"),
        "senderIdFront":     ("SENDER_ID",    "id"),
        "senderBankAccount": ("BANK_ACCT",    "account"),
        "recipientName":     ("RECIP_NAME",   "name"),
        "recipientAddress":  ("RECIP_ADDR",   "address"),
        "recipientContact":  ("RECIP_TEL",    "contact"),
        "recipientBizNo":    ("RECIP_BIZNO",  "bizno"),
        # 금액: 실제 값 로컬 보관, Claude에는 단위 힌트만
        "claimAmount":       None,
        "depositAmount":     None,
        "loanAmount":        None,
        "damageAmount":      None,
        "contractAmount":    None,
        "refundAmount":      None,
    }

    for key, value in mail_data.items():
        if key in MASK_RULES:
            rule = MASK_RULES[key]
            if rule is None:
                # 금액 필드
                mapping[key] = value
                if isinstance(value, (int, float)):
                    masked[key] = f"AMOUNT_{int(value) // 1_000_000}M"
                else:
                    masked[key] = "AMOUNT_UNDISCLOSED"
            else:
                token, _ = rule
                mapping[key] = value
                masked[key] = token
        else:
            # 비PII 필드는 그대로 전달 (이미 sanitize 완료)
            masked[key] = value

    _sessions[session_id] = {
        "mapping":    mapping,
        "created_at": datetime.now().isoformat(),
        "mail_type":  mail_data.get("mailType", "certified-mail"),
        "blocked":    blocked_fields,
    }

    result: dict = {
        "session_id": session_id,
        "masked":     masked,
        "message":    f"마스킹 완료. session_id={session_id}",
    }
    if blocked_fields:
        result["security_warning"] = (
            f"인젝션 패턴이 감지된 필드가 있어 [BLOCKED] 처리됐습니다: {blocked_fields}"
        )
    return result


# ══════════════════════════════════════════════════════════
# TOOL 3 — 내용증명 저장  (Layer 1 + Path Traversal 방어)
# ══════════════════════════════════════════════════════════

@mcp.tool()
def save_contract(
    session_id: str,
    contract_text: str,
    contract_type: str,
    output_dir: str = "",
) -> dict:
    """
    마스킹 토큰을 실제 값으로 복원하고 내용증명을 파일로 저장한다.

    보안 처리:
      - contract_text에 인젝션 패턴 검사 (저장 전 최종 검문)
      - output_dir Path Traversal 방지
      - contract_type 파일명 인젝션 방지
    """
    _expire_sessions()

    if session_id not in _sessions:
        return {"error": f"세션을 찾을 수 없습니다: {session_id}"}

    # contract_text sanitize (저장 직전 최종 검사)
    contract_text, injected = _sanitize_str(contract_text, field_name="contract_text")
    if injected:
        log.warning("save_contract injection_in_text session=%s", session_id)
        return {"error": "내용증명 텍스트에서 인젝션 패턴이 감지됐습니다. 내용을 확인하세요."}

    # 파일명 sanitize: 경로 구분자·특수문자 제거
    safe_type = re.sub(r"[^\w가-힣\-]", "_", contract_type)[:120]

    # output_dir 검증
    out_path, dir_warn = _validate_output_dir(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    session  = _sessions[session_id]
    mapping  = session["mapping"]

    # 마스킹 토큰 역치환
    restored = contract_text
    TOKEN_MAP = {
        "SENDER_NAME": mapping.get("senderName",       "[발신인 성명]"),
        "SENDER_ADDR": mapping.get("senderAddress",    "[발신인 주소]"),
        "SENDER_TEL":  mapping.get("senderContact",    "[발신인 연락처]"),
        "SENDER_ID":   mapping.get("senderIdFront",    "[주민번호 앞자리]"),
        "BANK_ACCT":   mapping.get("senderBankAccount","[계좌번호]"),
        "RECIP_NAME":  mapping.get("recipientName",    "[수신인 성명]"),
        "RECIP_ADDR":  mapping.get("recipientAddress", "[수신인 주소]"),
        "RECIP_TEL":   mapping.get("recipientContact", "[수신인 연락처]"),
        "RECIP_BIZNO": mapping.get("recipientBizNo",   "[사업자등록번호]"),
    }
    AMOUNT_KEYS = [
        "claimAmount","depositAmount","loanAmount",
        "damageAmount","contractAmount","refundAmount",
    ]
    for key in AMOUNT_KEYS:
        value = mapping.get(key)
        if value and isinstance(value, (int, float)):
            hint = f"AMOUNT_{int(value) // 1_000_000}M"
            restored = restored.replace(hint, f"{int(value):,}원")

    for token, real in TOKEN_MAP.items():
        restored = restored.replace(token, str(real))

    txt_path  = out_path / f"{safe_type}.txt"
    docx_path = out_path / f"{safe_type}.docx"

    txt_path.write_text(restored, encoding="utf-8")

    # DOCX 변환
    docx_result: dict
    if DOCX_GEN.exists():
        try:
            proc = subprocess.run(
                ["python3", str(DOCX_GEN), str(txt_path)],
                capture_output=True, text=True, timeout=30,
            )
            docx_result = (
                {"status": "success", "path": str(docx_path)}
                if proc.returncode == 0
                else {"status": "error", "stderr": proc.stderr[:300]}
            )
        except Exception as e:
            docx_result = {"status": "error", "reason": str(e)}
    else:
        docx_result = {"status": "skipped", "reason": "certified-mail-docx.py not found"}

    del _sessions[session_id]
    log.info("save_contract ok file=%s", txt_path.name)

    result: dict = {
        "txt_path":    str(txt_path),
        "docx_path":   str(docx_path) if docx_result.get("status") == "success" else None,
        "docx_result": docx_result,
        "message":     f"내용증명 저장 완료: {txt_path.name}",
    }
    if dir_warn:
        result["path_warning"] = dir_warn
    return result


# ══════════════════════════════════════════════════════════
# TOOL 4 — 파일 불러오기  (Layer 3 — 간접 인젝션 방어)
# ══════════════════════════════════════════════════════════

@mcp.tool()
def load_contract_for_review(file_path: str) -> dict:
    """
    기존 내용증명 파일을 불러와 PII 마스킹 후 반환한다.

    보안 처리:
      - 파일 경로 Path Traversal 방지
      - 파일 크기 상한 (200KB)
      - 줄 단위 인젝션 검사 + 비정상 긴 줄 차단
      - PII 패턴(전화·주민번호·계좌) 마스킹
    """
    _expire_sessions()

    # 파일 경로 검증
    try:
        path = Path(file_path).expanduser().resolve()
    except Exception:
        return {"error": f"경로 파싱 실패: {file_path!r}"}

    allowed = False
    for root in _ALLOWED_ROOT_PARENTS:
        try:
            path.relative_to(root.resolve())
            allowed = True
            break
        except ValueError:
            pass
    if not allowed:
        log.warning("load path_traversal path=%s", file_path)
        return {"error": f"허용되지 않은 경로입니다: {file_path!r}"}

    if not path.exists():
        return {"error": f"파일을 찾을 수 없습니다: {file_path}"}

    size = path.stat().st_size
    if size > _MAX_FILE_BYTES:
        return {"error": f"파일 크기 초과: {size:,}B (최대 {_MAX_FILE_BYTES:,}B)"}

    content = path.read_text(encoding="utf-8", errors="replace")

    # Layer 3: 줄 단위 검사 + 인젝션 차단
    content, file_warnings = _sanitize_file_content(content)

    # PII 패턴 마스킹
    PII_PATTERNS = [
        (r"\d{6}-[1-4]\d{6}",         "[주민등록번호]"),
        (r"\d{3}-\d{2}-\d{5}",        "[사업자번호]"),
        (r"\d{3,4}-\d{3,4}-\d{4}",    "[전화번호]"),
        (r"\d{4}\s?\d{4}\s?\d{4}\s?\d{4}", "[카드번호]"),
        (r"\d{10,16}",                 "[계좌번호 추정]"),
    ]
    masked_content = content
    for pattern, replacement in PII_PATTERNS:
        masked_content = re.sub(pattern, replacement, masked_content)

    session_id = uuid.uuid4().hex[:12]
    _sessions[session_id] = {
        "original_path":    str(path),
        "original_content": content,   # sanitize 완료본 보관 (PII 아직 복원 안된 상태)
        "mapping":          {},
        "created_at":       datetime.now().isoformat(),
        "mode":             "review",
    }

    log.info("load_contract_for_review ok file=%s warnings=%d", path.name, len(file_warnings))

    result: dict = {
        "session_id":     session_id,
        "masked_content": masked_content,
        "file_info": {
            "name":     path.name,
            "size":     size,
            "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        },
        "message": "파일 로드 완료. 개인정보 패턴이 마스킹됐습니다.",
    }
    if file_warnings:
        result["security_warnings"] = file_warnings
    return result


# ══════════════════════════════════════════════════════════
# TOOL 5 — 수정된 내용증명 저장
# ══════════════════════════════════════════════════════════

@mcp.tool()
def save_reviewed_contract(
    session_id: str,
    revised_text: str,
    output_dir: str = "",
) -> dict:
    """
    검토·수정된 내용증명을 저장한다.

    보안 처리:
      - revised_text 인젝션 검사
      - output_dir Path Traversal 방지
    """
    _expire_sessions()

    if session_id not in _sessions:
        return {"error": f"세션을 찾을 수 없습니다: {session_id}"}

    # 저장 텍스트 sanitize
    revised_text, injected = _sanitize_str(revised_text, field_name="revised_text")
    if injected:
        return {"error": "수정된 텍스트에서 인젝션 패턴이 감지됐습니다."}

    out_path, dir_warn = _validate_output_dir(output_dir)
    session       = _sessions[session_id]
    original_path = Path(session.get("original_path", ""))

    if not output_dir and original_path.parent != Path("."):
        out_path = original_path.parent

    out_path.mkdir(parents=True, exist_ok=True)

    stem      = original_path.stem or "certified-mail-revised"
    safe_stem = re.sub(r"[^\w가-힣\-]", "_", stem)[:120]
    txt_path  = out_path / f"{safe_stem}-revised.txt"
    docx_path = out_path / f"{safe_stem}-revised.docx"

    txt_path.write_text(revised_text, encoding="utf-8")

    docx_result: dict = {"status": "skipped"}
    if DOCX_GEN.exists():
        try:
            proc = subprocess.run(
                ["python3", str(DOCX_GEN), str(txt_path)],
                capture_output=True, text=True, timeout=30,
            )
            docx_result = (
                {"status": "success"}
                if proc.returncode == 0
                else {"status": "error", "stderr": proc.stderr[:300]}
            )
        except Exception as e:
            docx_result = {"status": "error", "reason": str(e)}

    del _sessions[session_id]
    log.info("save_reviewed_contract ok file=%s", txt_path.name)

    result: dict = {
        "txt_path":    str(txt_path),
        "docx_path":   str(docx_path) if docx_result.get("status") == "success" else None,
        "docx_result": docx_result,
        "message":     f"수정본 저장 완료: {txt_path.name}",
    }
    if dir_warn:
        result["path_warning"] = dir_warn
    return result


if __name__ == "__main__":
    mcp.run()
