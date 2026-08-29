# korean-certified-mail

> 한국 도메인 특화 AI **스페시아이**에서 만듭니다.
> 법률·세무·노무·회계·의료 실무에 쓰는 도구를 오픈소스로 공개하고,
> 완성 제품은 [speciai.kr](https://speciai.kr) 에서 운영합니다.
> → 제품 [speciai.kr/plugin](https://speciai.kr/plugin) · 커뮤니티 [디스코드](https://discord.gg/hqdGsY7UpH)

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
- 디스코드: https://discord.gg/hqdGsY7UpH
- GitHub Issues / PR 환영합니다.

---

## 라이선스

Apache-2.0

---

## 만든 곳

[스페시아이](https://speciai.kr)는 법률·세무·노무·회계·의료 실무에 쓰는
도메인 특화 AI를 만듭니다. 한국능률협회와 AI 교육과정을 공동 개설했고,
전문직 세미나에 누적 500명 이상이 참여했습니다.

- 제품 전체 — <https://speciai.kr/services>
- Claude Code 플러그인 (법무·노무·세무 자문) — <https://speciai.kr/plugin>
- 전문직 AI 세미나 (월 1회) — <https://speciai.kr/seminar>
- 커뮤니티 — <https://discord.gg/hqdGsY7UpH>

### 개발팀으로 쓰신다면

DevCowork — 화면설계서 기반 구현·코드리뷰, 커밋·토큰·DORA 지표 추적
→ <https://devcowork.speciai.team>

### 함께 만든 오픈소스

| 저장소 | 내용 | |
|---|---|---|
| [korean-privacy-terms](https://github.com/kimlawtech/korean-privacy-terms) | 처리방침·이용약관 자동 생성 | 573★ |
| [korean-jangbu-for](https://github.com/kimlawtech/korean-jangbu-for) | 장부 자동 생성·OCR | 82★ |
| [korean-contracts](https://github.com/kimlawtech/korean-contracts) | 한국 계약서 9종 | 61★ |
| [korean-patent-diagram](https://github.com/kimlawtech/korean-patent-diagram) | 특허 도면 자동 생성 (KIPO 규격) | 18★ |
| [korean-domain-agent](https://github.com/kimlawtech/korean-domain-agent) | 도메인 특화 LLM 에이전트 킷 | 5★ |
