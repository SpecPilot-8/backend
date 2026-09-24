import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extractor.pdf_screens import parse_pdf

path = sys.argv[1]
screens = parse_pdf(path)

print(f"총 {len(screens)}개 화면 추출\n")
for s in screens:
    low_conf = [k for k, v in s["header_confidence"].items() if v < 0.7]
    flag = f"  [LOW-CONF: {low_conf}]" if low_conf else ""
    print(f"{s['screen_id']:12s} | {s['screen_name']:15s} | Depth={s['depth']:10s} | "
          f"desc={len(s['description'])} action={len(s['actions'])}{flag}")

out_path = Path("/tmp/screens.json")
out_path.write_text(json.dumps(screens, ensure_ascii=False, indent=2))
print(f"\n전체 JSON: {out_path}")
