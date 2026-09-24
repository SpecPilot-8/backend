import sys
import pymupdf

path = sys.argv[1]
page_no = int(sys.argv[2]) if len(sys.argv) > 2 else 0

doc = pymupdf.open(path)
page = doc[page_no]
print(f"page size: {page.rect}")

blocks = page.get_text("blocks")
for b in blocks:
    x0, y0, x1, y1, text, block_no, block_type = b
    text = text.replace("\n", " \\n ").strip()
    print(f"[{block_no}] ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}) type={block_type} :: {text[:120]}")
