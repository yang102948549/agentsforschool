# agentsforschool

교사의 수업 아이디어를 지도안·학습활동지·Canva 슬라이드·형성평가로 발전시키는 **제미나이 활용 수업 설계 멀티에이전트** 지침입니다. Codex 같은 에이전트 도구에서 이 폴더를 열면 루트 `AGENTS.md`가 총괄 규칙으로 적용됩니다.

## 구성

```
AGENTS.md                 공통 정책·역할 표·핸드오프 게이트 요약
agents/coordinator/       총괄(사용자의 유일한 대화 창구, 배정·통합·G5 판정)
agents/lesson-ideas/      아이디어 기획            (G1 판정)
agents/lesson-plan/       수업 설계·지도안         (G2 판정)
agents/worksheets/        학습활동지               (G3 판정)
agents/canva-slides/      Canva 슬라이드           (G3 판정)
agents/formative-assessment/ 형성평가·피드백       (G3 판정)
agents/lesson-review/     독립 통합 검토           (G4 판정)
docs/gates.md             핸드오프 게이트 규칙(사람용)
gates/gates.json          게이트 항목 정의(기계용 원본)
tools/gate_check.py       게이트 기록 생성·검사 스크립트
tests/                    게이트 규칙 회귀 테스트
```

## 핸드오프 게이트

```
총괄 ─[G1]→ 아이디어 ─[G2]→ 지도안 ─[G3: 사용자 확인 + 명세 동결]→ 활동지/슬라이드/평가 ─[G4]→ 통합 검토 ─[G5]→ 전달
```

| 원칙 | 내용 |
|---|---|
| 받는 쪽 판정 | 판정자는 받는 담당입니다. 보낸 쪽 자기 판정은 무효입니다. |
| 규칙 계산 | pass/warning/block/not_assessed로 ready·conditional_ready·revision_required가 기계적으로 정해집니다. |
| 입력 고정 | 명세·입력 파일 SHA-256이 바뀌면 다시 검사합니다. |
| 명시적 확인 | G3는 사용자의 지도안 확인 발화가 기록되어야 통과합니다. |
| 반려 한도 | 같은 명세 버전에서 2회 반려 후에는 자동 반복을 멈추고 사용자에게 보고합니다. |

```bash
python tools/gate_check.py new G3 --lesson 수업명 --sender lesson-plan --receiver worksheets \
  --spec tmp/수업명/spec.md --spec-version v1
python tools/gate_check.py check tmp/수업명/gates/<기록>.json   # 0 통과 · 10 조건부 · 20 반려
python tools/gate_check.py verify --lesson 수업명 --through G4    # 기록 연쇄·명세 해시 확인
python tools/gate_check.py docs --write                           # gates.json → docs 항목표 생성
python -m unittest discover tests                                 # 규칙 테스트
```

## 사용 예

- "[학교급] [과목] [단원/주제] [차시 수], 제미나이 활용 수업 아이디어 추천해 줘."
- "2번 아이디어로 지도안 만들어 줘." → 지도안 확인 후 "이대로 진행해 주세요." → 자료 제작

## 주의

- 학생 자료·완성 수업 자료·`tmp/` 작업 파일은 저장소에 올리지 않습니다(`.gitignore`).
- 문서 파일만으로 에이전트가 자동 실행되지 않습니다. 실제 하위 에이전트 실행 여부와 게이트 기록 유무를 구분해 보고합니다.
