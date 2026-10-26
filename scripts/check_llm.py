"""Vercel 설정을 확인한다. --call을 붙일 때만 실제 유료 JSON 요청을 한 번 보낸다."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from llm import get_client

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
          "required": ["ok"], "additionalProperties": False}
ERROR_HELP = {
    "authentication_error": "Gateway 키가 유효한지 확인하세요.",
    "insufficient_credits": "Vercel의 사용 가능한 크레딧을 확인하세요.",
    "permission_denied": "키의 팀·모델 접근 권한을 확인하세요.",
    "model_not_found": "Gateway 모델 목록의 제공자/모델 ID를 확인하세요.",
    "invalid_request": "모델의 JSON Schema 지원과 요청 한도를 확인하세요.",
    "rate_limit": "요청 한도를 확인한 뒤 나중에 직접 다시 실행하세요.",
    "timeout": "응답 대기 시간을 넘었습니다. 자동으로 재호출하지 않았습니다.",
    "connection_error": "네트워크 연결을 확인하세요.",
    "max_tokens": "응답 토큰 한도에 도달했습니다. 성공 결과로 채택하지 않았습니다.",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--call", action="store_true", help="실제 유료 요청 1회 실행 (기본: 설정만 확인)")
    parser.add_argument("--max-tokens", type=int, default=256, help="진단 출력 토큰 한도 (64~4096, 기본 256)")
    args = parser.parse_args(argv)
    if not 64 <= args.max_tokens <= 4096:
        parser.error("--max-tokens는 64~4096이어야 합니다")
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
    if os.getenv("SPECPILOT_LLM_PROVIDER") != "vercel":
        print("설정 오류: 백엔드 .env에 SPECPILOT_LLM_PROVIDER=vercel을 설정하세요.", file=sys.stderr)
        return 2
    # 진단 요청은 문서/코드를 보내지 않고 작은 출력 한도로 제한한다.
    os.environ["SPECPILOT_LLM_MAX_TOKENS"] = str(args.max_tokens)
    try:
        client = get_client()
    except ValueError as error:
        print(f"설정 오류: {error}", file=sys.stderr)
        return 2
    print(f"설정 확인: vercel / {client.model}")
    if not args.call:
        print("형식 확인만 완료했습니다. 키 유효성/모델 접근은 미검증입니다. 실제 확인: --call")
        return 0
    result = client.complete_json(instructions="Return only the requested JSON object.", context="",
                                  prompt='Return {"ok": true}.', schema=SCHEMA)
    if result.error:
        print(f"연결 실패: {result.error}. {ERROR_HELP.get(result.error, '구조화 응답을 정상 확인하지 못했습니다.')}",
              file=sys.stderr)
        return 1
    if result.data != {"ok": True}:
        print("연결 실패: 기대한 JSON 값과 다릅니다.", file=sys.stderr)
        return 1
    print("실제 연결 및 JSON 검증 성공. 사용량: " + json.dumps(result.usage, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
