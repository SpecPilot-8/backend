"""기획서 PDF를 파싱해서 screen/requirement 테이블에 적재한다.

같은 stable_key가 이미 있으면 덮어쓴다 (기획서 재파싱 시 최신 결과로 갱신).
"""

import json
import re
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from extractor.pdf_screens import parse_pdf

DSN = "dbname=specpilot"


def guess_doc_version(path: str) -> str | None:
    m = re.search(r"ver([0-9]+(?:\.[0-9]+)?)", path, re.IGNORECASE)
    return f"ver{m.group(1)}" if m else None


def load(pdf_path: str) -> None:
    doc_version = guess_doc_version(pdf_path)
    screens = parse_pdf(pdf_path)

    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            for s in screens:
                cur.execute(
                    """
                    INSERT INTO screen
                        (screen_id, screen_name, depth, author, doc_version,
                         source_file, page_index, header_confidence, header_source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (screen_id) DO UPDATE SET
                        screen_name = EXCLUDED.screen_name,
                        depth = EXCLUDED.depth,
                        author = EXCLUDED.author,
                        doc_version = EXCLUDED.doc_version,
                        source_file = EXCLUDED.source_file,
                        page_index = EXCLUDED.page_index,
                        header_confidence = EXCLUDED.header_confidence,
                        header_source = EXCLUDED.header_source
                    """,
                    (
                        s["screen_id"], s["screen_name"], s["depth"], s["author"],
                        doc_version, pdf_path, s["page_index"],
                        json.dumps(s["header_confidence"]), json.dumps(s["header_source"]),
                    ),
                )

                rows = [(r, "description") for r in s["description"]] + \
                       [(r, "action") for r in s["actions"]]
                for r, kind in rows:
                    cur.execute(
                        """
                        INSERT INTO requirement
                            (stable_key, screen_id, doc_version, seq_label, kind, body,
                             parse_confidence, bbox)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (stable_key) DO UPDATE SET
                            body = EXCLUDED.body,
                            bbox = EXCLUDED.bbox,
                            doc_version = EXCLUDED.doc_version
                        """,
                        (
                            r["stable_key"], s["screen_id"], doc_version, r["seq_label"],
                            kind, r["body"], 1.0, json.dumps(r["bbox"]),
                        ),
                    )
        conn.commit()

    print(f"{len(screens)}개 화면 적재 완료 (doc_version={doc_version})")


if __name__ == "__main__":
    load(sys.argv[1])
