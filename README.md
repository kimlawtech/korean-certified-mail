# korean-certified-mail

> **SpeciAI** — 국내 최초·최대 한국 법률 AI 허브
> 창업자·전문직을 위한 법률 자동화 도구를 오픈소스로 만들고 있습니다.
> 웹사이트: **https://speciai.kr** | 커뮤니티: **https://discord.gg/3gYGuMcqgb** | @kimlawtech

---

한국 내용증명 자동 작성 Claude Code 스킬.

임금체불·보증금 반환·계약해지·손해배상 등 14개 유형을 지원하며,
법원 제출 기준 DOCX 출력, 법령 환각 방지, MCP 개인정보 보호가 내장됩니다.

---

## 특징

- **14개 유형** — 임금체불·보증금·대여금·손해배상·계약해지·이행촉구·하자보수·명도·갱신거절·환불·서비스·명예훼손·저작권·기타
- **법원 제출 기준 DOCX** — 바탕체 11pt, A4 좌3cm 우2.5cm, 줄간격 200%, 당사자 표·실선 자동 생성
- **법령 환각 방지** — 검증된 조항 허용 목록만 인용, 판례 번호 직접 인용 금지, 소멸시효 자동 경고
- **MCP 개인정보 보호** — 성명·주소·연락처·금액이 Claude 컨텍스트에 노출되지 않고 로컬에서만 처리
- **프롬프트 인젝션 방어** — 입력 sanitize(한국어·영어 18개 패턴), 파일 간접 인젝션 탐지, Path Traversal 방지
- **우체국 발송 가이드** — DOCX에 3부 출력 안내 자동 포함

---

## 보안 아키텍처

```
사용자 입력
    │
    ▼
[Layer 1] 입력 Sanitize (MCP 서버)
  · 한국어·영어 인젝션 패턴 18종 탐지 → [BLOCKED] 처리
  · 제어문자·유니코드 방향 제어 문자 제거
  · 파일 로드 시 줄 단위 검사 + 크기 제한 (200KB)
  · Path Traversal 방지 (허용 경로 외 차단)
    │
    ▼
[Layer 2] 개인정보 마스킹 (MCP 서버)
  · 이름 → SENDER_NAME / 주소 → SENDER_ADDR / 금액 → AMOUNT_3M
  · 마스킹된 토큰만 Claude 컨텍스트로 전달
    │
    ▼
Claude — 내용증명 초안 생성 (토큰만 보임)
    │
    ▼
[Layer 2 복원] 로컬에서 실제 값으로 역치환 → TXT + DOCX 저장
```

---

## 설치

### macOS / Linux

```bash
git clone https://github.com/kimlawtech/korean-certified-mail
cd korean-certified-mail
bash install.sh
```

### Windows (PowerShell)

```powershell
git clone https://github.com/kimlawtech/korean-certified-mail
cd korean-certified-mail
powershell -ExecutionPolicy Bypass -File install.ps1
```

설치 후 Claude Desktop을 재실행하면 MCP 서버가 자동 연결됩니다.

---

## 사용법

Claude Code에서:

```
/korean-certified-mail
```

또는 자연어로:

```
임금체불 내용증명 써줘
보증금 안 돌려주는 집주인한테 내용증명 보내고 싶어
계약 해지 통보 내용증명 만들어줘
```

---

## MCP 서버 없이 사용 (플레이스홀더 모드)

MCP 서버 없이도 내용증명 작성이 가능합니다.
단, 개인정보(성명·주소·금액 등)가 Claude 컨텍스트에 평문으로 노출됩니다.
민감 정보를 입력하지 않고 `[발신인 성명]` 형태의 플레이스홀더로 처리 후
생성된 파일에 직접 기입하는 방식을 권장합니다.

---

## DOCX 출력 사양

| 항목 | 사양 |
|------|------|
| 폰트 (한글) | 바탕체 |
| 폰트 (영문·숫자) | Times New Roman |
| 용지 | A4 / 좌3cm 우2.5cm 상하2.5cm |
| 줄간격 | 200% |
| 제목 | 16pt 굵게 가운데 정렬 |
| 본문 | 11pt 양쪽 정렬 |
| 당사자 | 2열 표, 라벨 회색 배경 |
| 구분선 | 2pt 실선 |

---

## 법적 면책

이 스킬이 생성하는 내용증명은 AI 초안입니다.
실제 발송 전 변호사·법무사의 검토를 권장합니다.
법적 전략과 판단은 전문가와 상의하십시오.

---

## 커뮤니티 및 기여

- 웹사이트: https://speciai.kr
- 디스코드: https://discord.gg/3gYGuMcqgb
- GitHub Issues / PR 환영합니다.

---

## 라이선스

Apache-2.0
