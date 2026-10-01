# 골든셋 채점 기록

판정 정책·분류 기준을 바꿀 때마다 같은 골든셋으로 채점한 결과를 남긴다.

## 골든셋

- 조건 480개 (Spring 240, React 240). decompose run 4의 조건 전체
- 2026-10-01 두 샘플 앱을 실행해 조건마다 확인한 결과 (`golden_label`, source `golden_worksheet_all_2026-10-01.xlsx`)
- 기준: `docs/label-definition.md`. 문구만 다르면 implemented(메모에 문구 다름).
  조건 문장이 "포커스아웃 시"를 요구할 때만 제출 시점 검사를 mismatch로 본다
- **한계:** 정답을 매긴 것도 Claude(판정과 같은 계열 모델)다. 사람이 표본을 다시 확인하기 전까지는 독립적인 정답으로 보기 어렵다.
  확인 절차는 행마다 메모에 있다

## 채점 방식

`python scripts/golden.py score [--runs repo=run ...]`

- **정확도는 판정을 시도한 조건만** 대상으로 한다. `not_statically_verifiable`은 도구가 판정하지 않겠다고 한 것이다
- 범위 밖으로 뺀 조건 수와 **그중 실제 결함 수를 따로** 적는다. 결함을 범위 밖으로 빼서 점수가 오르는 효과를 숨기지 않기 위해서다
- 결함 = 정답이 implemented가 아닌 것. "잡음"은 판정이 implemented도 needs_review도 아닌 경우

## 결과

| | 판정 실행 | 정책 / 분류 기준 | 판정 시도 | 일치 | 범위 밖 (그중 결함) | 결함 → 잡음 / 통과 / needs_review | 정상 → 경보 |
|---|---|---|---|---|---|---|---|
| Spring 전 | 11 | judge-v4 / decompose-v2 | 226 | 185 (82%) | 14 (2) | 89 → 63 / **16** / 10 | 137 → 1 |
| Spring 후 | 13 | judge-v5 / decompose-v3 | 218 | 179 (82%) | 22 (10) | 81 → 62 / **7** / 12 | 137 → 6 |
| React 전 | 12 | judge-v4 / decompose-v2 | 226 | 181 (80%) | 14 (2) | 78 → 51 / **16** / 11 | 148 → 10 |
| React 후 | 14 | judge-v5 / decompose-v3 | 218 | 184 (84%) | 22 (6) | 74 → 57 / **5** / 12 | 144 → 10 |

전후로 판정 시도 조건이 달라서(226 → 218), 양쪽 모두 판정한 조건만 놓고 다시 비교하면 아래와 같다.

| 공통 218개 | 일치 | 결함을 implemented로 통과 | 정상을 결함으로 경보 | 맞게 바뀜 / 틀리게 바뀜 |
|---|---|---|---|---|
| Spring | 183 (83.9%) → 179 (82.1%) | 10 → 7 | 1 → 6 | 5 / 9 |
| React | 177 (81.2%) → 184 (84.4%) | 12 → 5 | 10 → 10 | 12 / 5 |

원본 출력: `output/score_before_runs11-12.txt`, `output/score_after_judge-v5.txt`, `output/score_compare_11-12_vs_13-14.txt` (output/은 git 제외)

## 무엇을 바꿨나 (2026-10-01)

1. **판정 정책 judge-v5** (`verdict/rules.py`): 기획서의 팝업을 코드가 화면 안 문구로 보여주면(또는 그 반대) mismatch
2. **분류 기준 decompose-v3** (`decomposer/decompose.py` `VERIFIABLE_CRITERIA`): 화면상 위치·배치와 CSS가 그리는
   상태 표시(토글 손잡이 이동, 스위치 좌우 의미)를 not_statically_verifiable로. 기존 조건은
   `scripts/reclassify_verifiable.py`로 verifiable만 재분류 (골든셋을 읽지 않음, 기록은 `verifiable_change`)

## 해석

**팝업 규칙은 의도대로 작동했다.** 마이페이지 비밀번호 변경 팝업 조건에서 React 6개가 모두 implemented → mismatch로
바뀌었고, Spring도 판정 2개가 implemented → mismatch로, 1개가 partial → mismatch로 바뀌었다.
"결함을 implemented로 통과"가 React 12 → 5로 줄어든 것은 대부분 이 규칙의 효과다.

**재분류는 결함 통과를 줄였지만 결함 일부를 범위 밖으로 옮긴 것이기도 하다.** Spring에서 범위 밖으로 간 8개는 전부
실제 결함이고, 그중 6개는 이전 판정이 implemented로 통과시킨 것이다. React는 8개 중 4개가 결함이다. 결과적으로
범위 밖 결함이 Spring 2 → 10, React 2 → 6으로 늘었다. 이 조건들은 판정 대신 "화면 확인 필요"로 사람에게 넘겨야 한다.

**Spring 점수 하락의 원인은 아직 구분하지 못했다.** 틀리게 바뀐 9개 중 팝업 규칙과 관련 있어 보이는 것은
MYPAGE-002-C "팝업이 닫힌다"(not_found → mismatch) 정도다. 나머지(목록 정렬, 빈 목록 문구, 저장 버튼 활성화 기준 등)는
이번에 바꾼 규칙과 무관한 조건이다. 이전에도 같은 정책을 다시 돌리면 판정의 약 14%가 바뀌었다(README 진행 상태).
이번 변화가 규칙 효과인지 실행마다 생기는 흔들림인지 가리려면 같은 정책으로 여러 번 돌려 분산을 재야 한다.

**재분류도 실행마다 흔들렸다.** 같은 기준으로 dry-run을 두 번째로 돌렸을 때는 12개가 바뀌었는데, 실제 반영할 때는
스위치 좌우 의미 4개(조건 644, 646, 731, 733)가 code로 남아 8개만 바뀌었다. 원하는 결과가 나올 때까지 다시 돌리지 않고
반영된 결과를 그대로 썼다. 첫 dry-run에서는 기준과 어긋나게 비밀번호 가림/표시 조건까지 옮겨서, 기준 문장을
더 분명히 고친 뒤 다시 돌렸다(`output/reclassify_dryrun*.txt`).
