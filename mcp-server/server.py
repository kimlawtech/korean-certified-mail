#!/usr/bin/env python3
"""
korean-certified-mail MCP Server

보안 레이어 3종:
  Layer 1 — 입력 sanitize: 인젝션 패턴 차단 + 제어문자 제거
  Layer 2 — 개인정보 마스킹: Claude 컨텍스트에 PII 미전달
  Layer 3 — 파일 검증: load_contract_for_review 간접 인젝션 방어

추가 기능:
  증거 수집 — 텍스트/파일/URL 증거 등록 및 정리
  법령 교차검증 — legal-db.json 기반 인용 검증
  법제처 API — OPEN_API_KEY 환경변수 설정 시 실시간 조회
"""

import re
import uuid
import json
import logging
import mimetypes
import os
import subprocess
import urllib.request
import urllib.parse
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
LEGAL_DB   = BASE_DIR / "certified-mail" / "references" / "legal-db.json"

# ── 법령 DB 로드 ──────────────────────────────────────────
_legal_db: dict = {}
try:
    if LEGAL_DB.exists():
        _legal_db = json.loads(LEGAL_DB.read_text(encoding="utf-8"))
except Exception as _e:
    logging.warning("legal-db.json 로드 실패: %s", _e)

# ── 법제처 API 설정 ───────────────────────────────────────
_LAWGO_API_KEY = os.environ.get("LAWGO_API_KEY", "")
_LAWGO_BASE    = "https://www.law.go.kr/DRF"

# ── 증거 저장소 ───────────────────────────────────────────
_evidence_store: dict[str, list[dict]] = {}  # session_id → [evidence]

# 허용 증거 파일 확장자
_ALLOWED_EVIDENCE_EXT = {
    ".txt", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".docx", ".xlsx", ".csv", ".hwp", ".hwpx",
    ".mp4", ".mov", ".mp3", ".m4a",
    ".zip",
}
_MAX_EVIDENCE_FILE_MB = 50

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
        _evidence_store.pop(sid, None)
    if expired:
        log.info("session_expired count=%d", len(expired))


def _get_category_db(category: str) -> dict:
    """legal-db.json에서 카테고리 데이터 반환."""
    return _legal_db.get("categories", {}).get(category, {})


def _validate_citation(citation: str, category: str) -> dict:
    """
    인용 조항이 legal-db.json 허용 목록에 있는지 교차 검증.
    반환: {"valid": bool, "found": dict|None, "reason": str}
    """
    cat = _get_category_db(category)
    statutes = cat.get("statutes", [])

    # 판례 번호 패턴 검사
    if re.search(r"(대법원|헌법재판소|고등법원|지방법원)\s*\d{4}", citation):
        return {"valid": False, "found": None, "reason": "판례 번호 직접 인용 금지"}

    # DB 내 조항 탐색
    for s in statutes:
        if s.get("citation", "") in citation or citation in s.get("citation", ""):
            return {"valid": True, "found": s, "reason": "허용 목록 확인"}

    # 전체 카테고리 검색 (카테고리 불명확 시)
    if not category:
        for cat_data in _legal_db.get("categories", {}).values():
            for s in cat_data.get("statutes", []):
                if s.get("citation", "") in citation or citation in s.get("citation", ""):
                    return {"valid": True, "found": s, "reason": "허용 목록 확인 (타 카테고리)"}

    return {"valid": False, "found": None, "reason": "허용 목록에 없는 조항 — 인용 전 확인 필요"}


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


# ══════════════════════════════════════════════════════════
# TOOL 6 — 증거 등록 (텍스트·파일·URL)
# ══════════════════════════════════════════════════════════

@mcp.tool()
def add_evidence(
    session_id: str,
    evidence_type: str,
    description: str,
    content: str = "",
    file_path: str = "",
    url: str = "",
) -> dict:
    """
    내용증명 세션에 증거를 등록한다.

    evidence_type: "text" | "file" | "url"
      - text: content 필드에 직접 입력 (카카오톡 캡처 내용, 문자 내용 등)
      - file: file_path 필드에 로컬 파일 경로 (사진, PDF, 계약서 등)
      - url:  url 필드에 URL (온라인 게시물, 뉴스 기사 등)

    description: 증거 설명 (예: "2024-03-15 카카오톡 대화 — 대여 합의 확인")
    """
    _expire_sessions()

    if session_id not in _sessions and session_id not in _evidence_store:
        _evidence_store[session_id] = []

    if session_id not in _evidence_store:
        _evidence_store[session_id] = []

    description, desc_injected = _sanitize_str(description, "evidence_description")
    if desc_injected:
        return {"error": "증거 설명에서 인젝션 패턴이 감지됐습니다."}

    evidence_type = evidence_type.strip().lower()
    ev: dict = {
        "id":          uuid.uuid4().hex[:8],
        "type":        evidence_type,
        "description": description,
        "added_at":    datetime.now().isoformat(),
        "status":      "registered",
    }

    if evidence_type == "text":
        content, c_injected = _sanitize_str(content, "evidence_content")
        if c_injected:
            return {"error": "증거 내용에서 인젝션 패턴이 감지됐습니다."}
        if len(content) > 10_000:
            content = content[:10_000] + "[...]"
        ev["preview"] = content[:200] + ("..." if len(content) > 200 else "")
        ev["full_content"] = content

    elif evidence_type == "file":
        path, err = _validate_output_dir(str(Path(file_path).parent))
        if err:
            pass  # 읽기 전용이므로 경고만

        try:
            fp = Path(file_path).expanduser().resolve()
        except Exception:
            return {"error": f"파일 경로 파싱 실패: {file_path!r}"}

        allowed = False
        for root in _ALLOWED_ROOT_PARENTS:
            try:
                fp.relative_to(root.resolve())
                allowed = True
                break
            except ValueError:
                pass
        if not allowed:
            log.warning("evidence path_traversal path=%s", file_path)
            return {"error": f"허용되지 않은 경로입니다: {file_path!r}"}

        if not fp.exists():
            return {"error": f"파일을 찾을 수 없습니다: {file_path}"}

        ext = fp.suffix.lower()
        if ext not in _ALLOWED_EVIDENCE_EXT:
            return {"error": f"허용되지 않는 파일 형식입니다: {ext}"}

        size_mb = fp.stat().st_size / (1024 * 1024)
        if size_mb > _MAX_EVIDENCE_FILE_MB:
            return {"error": f"파일 크기 초과: {size_mb:.1f}MB (최대 {_MAX_EVIDENCE_FILE_MB}MB)"}

        mime = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        ev["file_path"]  = str(fp)
        ev["file_name"]  = fp.name
        ev["file_size"]  = f"{size_mb:.2f}MB"
        ev["mime_type"]  = mime

        # 텍스트 파일은 미리보기 추출
        if ext in {".txt", ".csv"} and size_mb < 1:
            try:
                raw = fp.read_text(encoding="utf-8", errors="replace")[:500]
                ev["preview"] = raw
            except Exception:
                pass

    elif evidence_type == "url":
        url_clean, u_injected = _sanitize_str(url, "evidence_url")
        if u_injected:
            return {"error": "URL에서 인젝션 패턴이 감지됐습니다."}
        if not url_clean.startswith(("http://", "https://")):
            return {"error": "http:// 또는 https:// 로 시작하는 URL을 입력하세요."}
        ev["url"] = url_clean
        ev["preview"] = url_clean

    else:
        return {"error": f"지원하지 않는 증거 유형입니다: {evidence_type!r}. text | file | url 중 선택하세요."}

    _evidence_store[session_id].append(ev)
    log.info("add_evidence session=%s type=%s id=%s", session_id, evidence_type, ev["id"])

    return {
        "evidence_id":    ev["id"],
        "session_id":     session_id,
        "total_evidence": len(_evidence_store[session_id]),
        "message":        f"증거 등록 완료 (ID: {ev['id']})",
    }


# ══════════════════════════════════════════════════════════
# TOOL 7 — 증거 목록 조회 및 사실관계 정리
# ══════════════════════════════════════════════════════════

@mcp.tool()
def get_evidence_summary(session_id: str) -> dict:
    """
    등록된 증거 목록과 사실관계 인용 포인트를 반환한다.
    Claude가 사실관계 섹션 작성 시 참조하도록 구조화된 형태로 반환.
    """
    _expire_sessions()
    items = _evidence_store.get(session_id, [])

    if not items:
        return {
            "session_id":    session_id,
            "evidence_count": 0,
            "summary":       "등록된 증거가 없습니다.",
            "items":         [],
        }

    formatted = []
    for i, ev in enumerate(items, start=1):
        entry: dict = {
            "번호":   i,
            "ID":     ev["id"],
            "유형":   ev["type"],
            "설명":   ev["description"],
            "등록일": ev["added_at"][:10],
        }
        if "preview" in ev:
            entry["내용미리보기"] = ev["preview"]
        if "file_name" in ev:
            entry["파일명"] = ev["file_name"]
            entry["크기"]   = ev["file_size"]
        if "url" in ev:
            entry["URL"] = ev["url"]
        formatted.append(entry)

    return {
        "session_id":     session_id,
        "evidence_count": len(items),
        "items":          formatted,
        "drafting_note":  (
            "위 증거를 바탕으로 사실관계 섹션에서 "
            "날짜·당사자·행위를 구체적으로 기술하고, "
            "각 사실에 해당 증거 설명을 괄호로 병기하세요. "
            "예: (증거 1: 2024-03-15 카카오톡 대화)"
        ),
    }


# ══════════════════════════════════════════════════════════
# TOOL 8 — 법령 교차검증
# ══════════════════════════════════════════════════════════

@mcp.tool()
def verify_legal_citations(
    citations: list,
    category: str = "",
) -> dict:
    """
    내용증명에 인용할 법령 조항 목록을 legal-db.json 허용 목록과 교차 검증한다.

    citations: ["민법 제390조", "근로기준법 제36조", ...] 형태의 리스트
    category:  wage-claim | deposit-return | loan-return | damage-claim |
               contract-termination | performance-demand | defect-repair |
               eviction-demand | renewal-refusal | refund-claim |
               service-demand | defamation-warning | copyright-warning
               (비워두면 전체 DB에서 탐색)
    """
    if not _legal_db:
        return {
            "error": "legal-db.json 로드 실패 — 법령 DB 파일을 확인하세요.",
            "db_path": str(LEGAL_DB),
        }

    results = []
    for citation in citations:
        citation_clean, _ = _sanitize_str(str(citation), "citation")
        result = _validate_citation(citation_clean, category)
        entry: dict = {
            "citation": citation_clean,
            "valid":    result["valid"],
            "reason":   result["reason"],
        }
        if result["found"]:
            found = result["found"]
            entry["summary"]  = found.get("summary", "")
            entry["template"] = found.get("template")
            entry["note"]     = found.get("note")
        results.append(entry)

    valid_count   = sum(1 for r in results if r["valid"])
    invalid_count = len(results) - valid_count

    # 금지 패턴 검사
    forbidden = _legal_db.get("cross_validation_rules", {}).get("forbidden", [])
    forbidden_found = []
    for citation in citations:
        c = str(citation)
        for f in forbidden:
            if "판례 번호" in f and re.search(r"(대법원|헌법재판소)\s*\d{4}", c):
                forbidden_found.append({"citation": c, "rule": f})

    return {
        "category":       category or "전체",
        "total":          len(results),
        "valid_count":    valid_count,
        "invalid_count":  invalid_count,
        "results":        results,
        "forbidden_found": forbidden_found,
        "recommendation": (
            "invalid 항목은 인용을 생략하거나 사용자 확인 후 사용하세요. "
            "판례 번호는 내용증명에서 절대 직접 인용하지 마세요."
        ) if invalid_count > 0 else "모든 조항이 허용 목록에 있습니다.",
    }


# ══════════════════════════════════════════════════════════
# TOOL 9 — 법제처 API 실시간 법령 조회
# ══════════════════════════════════════════════════════════

@mcp.tool()
def search_law_api(
    query: str,
    search_type: str = "statute",
    page: int = 1,
) -> dict:
    """
    법제처 국가법령정보 Open API로 실시간 법령·판례 조회.

    사용하려면 환경변수 LAWGO_API_KEY 설정 필요:
      export LAWGO_API_KEY=<법제처 Open API 키>
      (발급: https://open.law.go.kr → 오픈API → 인증키 발급)

    search_type: "statute" (법령) | "precedent" (판례) | "regulation" (행정규칙)
    query: 검색어 (예: "주택임대차보호법", "임금체불 판례")
    page: 페이지 번호 (기본 1)
    """
    if not _LAWGO_API_KEY:
        return {
            "status":  "api_key_missing",
            "message": (
                "법제처 API 키가 설정되지 않았습니다. "
                "LAWGO_API_KEY 환경변수를 설정하면 실시간 법령 조회가 가능합니다. "
                "발급: https://open.law.go.kr → 오픈API → 인증키 발급"
            ),
            "fallback": "local_db",
            "local_db_available": bool(_legal_db),
        }

    query_clean, q_injected = _sanitize_str(query, "law_query")
    if q_injected:
        return {"error": "검색어에서 인젝션 패턴이 감지됐습니다."}

    # API 엔드포인트 분기
    type_map = {
        "statute":    ("lawSearch.do",   "법령"),
        "precedent":  ("prcdSearch.do",  "판례"),
        "regulation": ("admRulSearch.do","행정규칙"),
    }
    endpoint_file, type_label = type_map.get(search_type, type_map["statute"])

    params = urllib.parse.urlencode({
        "OC":       _LAWGO_API_KEY,
        "target":   search_type,
        "type":     "JSON",
        "query":    query_clean,
        "page":     page,
        "display":  10,
    })
    url = f"{_LAWGO_BASE}/{endpoint_file}?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "korean-certified-mail/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.warning("lawgo_api_error query=%s err=%s", query_clean, e)
        return {
            "status":  "api_error",
            "message": f"법제처 API 호출 실패: {e}",
            "fallback": "local_db 사용을 권장합니다.",
        }

    items = data.get("LawSearch", {}).get("law", []) or \
            data.get("PrcdSearch", {}).get("prcd", []) or \
            data.get("AdmRulSearch", {}).get("admRul", []) or []

    total_count = (
        data.get("LawSearch", {}).get("@totalCnt", 0) or
        data.get("PrcdSearch", {}).get("@totalCnt", 0) or
        data.get("AdmRulSearch", {}).get("@totalCnt", 0) or 0
    )

    results = []
    for item in items[:10]:
        entry: dict = {}
        if search_type == "statute":
            entry = {
                "법령명":   item.get("법령명한글", ""),
                "법령ID":   item.get("법령ID", ""),
                "공포일자": item.get("공포일자", ""),
                "시행일자": item.get("시행일자", ""),
                "소관부처": item.get("소관부처명", ""),
            }
        elif search_type == "precedent":
            entry = {
                "사건명":   item.get("사건명", ""),
                "법원명":   item.get("법원명", ""),
                "선고일자": item.get("선고일자", ""),
                "요지":     item.get("판시사항", "")[:200] if item.get("판시사항") else "",
                "주의":     "판례 번호는 내용증명에서 직접 인용 금지 — 요지만 참조하세요.",
            }
        else:
            entry = {
                "규칙명": item.get("행정규칙명", ""),
                "기관":   item.get("제개정기관명", ""),
            }
        results.append(entry)

    log.info("lawgo_api ok query=%s type=%s count=%d", query_clean, search_type, len(results))

    return {
        "status":      "success",
        "type":        type_label,
        "query":       query_clean,
        "total_count": total_count,
        "page":        page,
        "results":     results,
        "caution":     (
            "판례 조회 결과는 참고용입니다. "
            "내용증명에서 판례 번호 직접 인용은 금지됩니다 — 판례 요지만 참조하세요."
        ) if search_type == "precedent" else None,
    }


# ══════════════════════════════════════════════════════════
# TOOL 10 — 사실관계 정리 및 타임라인 생성
# ══════════════════════════════════════════════════════════

@mcp.tool()
def organize_facts(
    session_id: str,
    facts: list,
    category: str = "",
) -> dict:
    """
    사용자로부터 수집한 사실관계를 시간순으로 정리하고
    내용증명 작성에 필요한 구조화된 형태로 반환한다.

    facts: [
      {"date": "2024-03-15", "actor": "발신인", "action": "카카오뱅크로 300만원 이체"},
      {"date": "2024-09-30", "actor": "계약 만료", "action": "임대차 계약 종료"},
      ...
    ]
    category: 내용증명 유형 (wage-claim, deposit-return 등)
    """
    _expire_sessions()

    if not facts:
        return {"error": "사실관계 항목이 없습니다. facts 배열을 채워서 전달하세요."}

    # sanitize
    cleaned_facts = []
    for f in facts:
        if not isinstance(f, dict):
            continue
        date,   _ = _sanitize_str(str(f.get("date",   "")), "fact_date")
        actor,  _ = _sanitize_str(str(f.get("actor",  "")), "fact_actor")
        action, _ = _sanitize_str(str(f.get("action", "")), "fact_action")
        cleaned_facts.append({"date": date, "actor": actor, "action": action})

    # 날짜순 정렬 (파싱 실패 항목은 뒤로)
    def parse_date(f: dict):
        d = f["date"].replace(". ", "-").replace(".", "")
        try:
            return datetime.strptime(d.strip(), "%Y-%m-%d")
        except Exception:
            return datetime.max

    sorted_facts = sorted(cleaned_facts, key=parse_date)

    # 소멸시효 경고 계산
    warnings = []
    cat_db = _get_category_db(category)
    sol = cat_db.get("statute_of_limitations", "")

    # 가장 오래된 날짜 기준 시효 임박 체크
    oldest_date = None
    for f in sorted_facts:
        d = f["date"].replace(". ", "-").replace(".", "").strip()
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
            if oldest_date is None or dt < oldest_date:
                oldest_date = dt
        except Exception:
            pass

    if oldest_date:
        elapsed_days = (datetime.now() - oldest_date).days
        if elapsed_days > 900:  # 3년 근접
            warnings.append(f"소멸시효 임박 주의: 기산일({oldest_date.strftime('%Y.%m.%d')})로부터 {elapsed_days}일 경과. {sol}")
        elif elapsed_days > 3000:  # 10년 근접
            warnings.append(f"소멸시효 경과 가능성: {elapsed_days}일 경과 — 전문가 상담 권장")

    # 타임라인 텍스트
    timeline_lines = []
    for f in sorted_facts:
        line = f"{f['date']}  [{f['actor']}]  {f['action']}"
        timeline_lines.append(line)

    # 세션에 저장
    if session_id in _sessions:
        _sessions[session_id]["facts"] = sorted_facts

    # 내용증명 사실관계 초안 힌트
    drafting_hint = (
        f"위 {len(sorted_facts)}개 사실을 바탕으로 내용증명 사실관계 섹션을 작성하세요. "
        "각 사실은 '발신인 [이름]은 [날짜]에 [행위]하였습니다.' 형식으로 기술하고, "
        "해당 증거가 있으면 괄호로 병기하세요."
    )

    return {
        "session_id":     session_id,
        "fact_count":     len(sorted_facts),
        "timeline":       timeline_lines,
        "sorted_facts":   sorted_facts,
        "sol_info":       sol,
        "warnings":       warnings,
        "drafting_hint":  drafting_hint,
    }


if __name__ == "__main__":
    mcp.run()
