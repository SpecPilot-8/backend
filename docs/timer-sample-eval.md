# 타이머 샘플로 본 판정 일반화

씽크트리 샘플이 아닌 처음 보는 앱과 기획서에서도 판정이 맞는지 확인했다(2026-11-13, API 사용, 약 $0.5 추정).
지금까지 골든셋은 씽크트리 기획서 1종뿐이었다(CLAUDE.md 9장의 한계).

## 입력

- 기획서 `SpecPilot_Test_Sample_Timer.pdf`(팀원 작성): 화면 5개, 요구사항 7개
- 코드 `specpilot-test-time-react`: React 단독, 화면당 파일 1개, 225줄, 서버 없음
- 정답지 `SpecPilot_Test_Sample_Timer_AnswerSheet.pdf`: 조건 21개, 요구사항 단위 완료 4 / 부분 2 / 미수행 1

## 방법

기획서 양식이 달라 파서가 화면·요구사항을 나누지 못한다(웹 파서는 텍스트 32블록을 읽고 "지원 화면 표 구조를
확인할 수 없습니다" 경고를 낸다). 파서 일반화와 판정 일반화를 섞지 않으려고 요구사항은 사람이 옮겨 넣고
판정 단계만 쟀다(추출과 판정 분리 측정, 9장). 기존 결과와 섞이지 않게 별도 DB를 썼다.

```bash
createdb specpilot_timer && psql -d specpilot_timer -f db/schema.sql
export SPECPILOT_DSN="dbname=specpilot_timer"
python db/load_requirements.py output/timer/requirements.json      # 사람이 옮긴 요구사항
python db/load_code.py <샘플 경로>/specpilot-test-time-react timer-react
python scripts/run_decompose.py                                    # 조건 22개
python scripts/run_match.py full_context timer-react
python scripts/tag_scope.py timer-react
python scripts/run_verdict.py timer-react
```

진입 파일 매핑은 `matcher/scope/entries/timer-react.json`. 판정 프롬프트·규칙은 씽크트리 샘플과 같다(judge-v5).

## 결과

조건 분해는 22개로, 정답지보다 1개 많다(기록 화면의 '시작 시간'과 '집중 시간'을 따로 나눔). 정답지 조건 기준으로 맞췄다.

| 요구사항 | 정답지 | SpecPilot | 일치 |
|---|---|---|---|
| TIMER-001 A 시작 | 3/3 완료 | 시작 **partial** · 상태 implemented · 버튼 비활성 implemented | 2/3 |
| TIMER-001 B 정지 | 2/3 부분 | 정지 implemented · 시간 유지 implemented · 알림 not_found | 3/3 |
| TIMER-002 1 시간 설정 | 3/3 완료 | 범위 · 숫자 · 빈 값 모두 implemented | 3/3 |
| TIMER-002 2 알람음 | 0/3 미수행 | 3개 모두 not_found | 3/3 |
| TIMER-003 1 기록 | 3/3 완료 | 정렬 · 표시 implemented · 페이징 **partial** | 2/3 |
| TIMER-004 1 통계 | 1/3 부분 | 누적 시간 implemented · 요일 강조 · 증감률 not_found | 3/3 |
| TIMER-005 A 휴식 | 3/3 완료 | 즉시 종료 **partial** · 횟수 기록 **partial** · 이동 implemented | 1/3 |

- **조건 일치 17/21 (81%).** 씽크트리 샘플(Spring 82%, React 84%, docs/golden-score.md)과 비슷하다.
- **정답지가 '누락'이라 한 6개 조건은 모두 not_found로 잡았다(6/6).** 누락을 implemented로 통과시킨 경우는 0건이다.
- 어긋난 4건은 모두 정답지가 '완료', SpecPilot이 partial이다. 더 엄격한 쪽으로만 어긋났다.

## 어긋난 4건: 누가 맞나

정답지는 "코드에 해당 모양이 있으면 완료"로 매겼고, SpecPilot은 "근거가 있다 ≠ 기능이 동작한다"(CLAUDE.md 8.2)로 판정한다.

| 조건 | SpecPilot 사유 | 판단 |
|---|---|---|
| 001 A 타이머가 시작된다 | 25로 세팅만 하고 시간이 줄어드는 로직(setInterval 등)이 없다 | SpecPilot이 맞다 |
| 003 페이지에 10개씩 페이징 | slice는 있지만 page가 1로 고정되고 페이지 이동 UI가 없다 | SpecPilot이 맞다 |
| 005 횟수가 1 증가해 서버에 기록 | 횟수가 화면 로컬 state라 들어올 때마다 0에서 시작해 항상 1을 보낸다 | SpecPilot이 맞다 |
| 005 휴식 시간을 즉시 종료 | 휴식 타이머 자체가 없어 '종료' 처리를 확인할 수 없다 | 해석에 따라 다름 |

SpecPilot이 사유에 남기고 판정에는 반영하지 않은 것도 있다. 상태 표시가 '진행중'이 아니라 `RUNNING`이고,
기록과 누적 시간은 하드코딩된 값이다. 이 둘은 정답지와 같이 implemented로 판정했다.

## 한계

- 요구사항 7개, 조건 21개라 정확도 숫자로 일반화를 주장하기엔 작다. "처음 보는 앱에서도 누락은 모두 잡고,
  틀린 '구현됨'은 없었다"는 사례로 쓴다.
- 정답지 작성자와 판정 기준(모양 vs 동작)이 달라 일치율은 기준 차이를 포함한다.
- 파서는 이 양식을 화면 단위로 나누지 못한다. 양식 하나에 맞춰 파서를 고치는 건 일반화가 아니라서 하지 않았다.
- 1회 실행이다. 같은 입력에도 판정이 조금씩 흔들린다(docs/golden-score.md).
