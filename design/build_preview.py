"""Render the .dc.html artboards into one static preview page.

The design canvas itself needs Node to assemble; this is a plain-browser stand-in so the
artboards can be looked at without it. Each artboard is inlined in its own iframe so their
stylesheets cannot leak into one another, exactly as they are isolated on the real canvas.
"""
import html
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULTS = {"accent": "#d97757", "priceColor": "#e89070"}


def to_page(path: str) -> str:
    """One .dc.html -> a standalone HTML document a browser can render."""
    src = open(path, encoding="utf-8").read()
    src = src.replace('<script src="./support.js"></script>', "")
    src = re.sub(r"<script data-dc-script.*?</script>", "", src, flags=re.S)
    src = src.replace("<x-dc>", "").replace("</x-dc>", "")
    src = src.replace("<helmet>", "").replace("</helmet>", "")
    for name, value in DEFAULTS.items():
        src = src.replace("{{" + name + "}}", value)
    left = re.findall(r"\{\{[^}]*\}\}", src)
    if left:
        print(f"  warning: {os.path.basename(path)} still has holes: {sorted(set(left))}")
    return src


def main():
    canvas = json.load(open(os.path.join(HERE, "canvas.json"), encoding="utf-8"))
    boards = canvas["artboards"]
    notes = {n["id"]: n["text"] for n in canvas.get("annotations", [])}

    board_dir = os.path.join(HERE, "boards")
    os.makedirs(board_dir, exist_ok=True)

    blocks = []
    for b in boards:
        page = to_page(os.path.join(HERE, b["file"]))
        # also write each board on its own, so any one can be opened directly
        single = b["file"].replace(".dc.html", ".html")
        open(os.path.join(board_dir, single), "w", encoding="utf-8").write(page)
        blocks.append(
            f'<section class="board">'
            f'<div class="cap"><span class="name">{html.escape(b.get("title") or b["file"])}</span>'
            f'<span class="dim">{b["w"]} x {b["h"]}  ·  {html.escape(b["file"])}</span></div>'
            f'<iframe style="width:{b["w"]}px;height:{b["h"]}px" '
            f'srcdoc="{html.escape(page, quote=True)}"></iframe>'
            f"</section>"
        )

    note_html = "".join(
        f'<p class="note">{html.escape(t)}</p>' for t in notes.values()
    )
    out = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Item Hunter UI preview</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&amp;family=Lora:wght@400;500&amp;display=swap">
<style>
  body {{ margin: 0; background: #0c0c0b; color: #faf9f5;
         font: 400 14px/1.5 "Poppins", Arial, sans-serif; padding: 40px; }}
  h1 {{ font-size: 22px; font-weight: 600; letter-spacing: -0.02em; margin: 0 0 6px; }}
  .lede {{ font-family: "Lora", Georgia, serif; color: #b0aea5; max-width: 760px; margin: 0 0 18px; }}
  .note {{ font-family: "Lora", Georgia, serif; color: #b0aea5; background: #1d1d1b;
           border: 1px solid #33322e; border-radius: 8px; padding: 10px 14px;
           max-width: 560px; margin: 0 0 10px; white-space: pre-line; font-size: 13px; }}
  .board {{ margin: 34px 0 0; }}
  .cap {{ display: flex; align-items: baseline; gap: 12px; margin-bottom: 8px; }}
  .name {{ font-weight: 600; font-size: 15px; }}
  .dim {{ color: #7c7a72; font-size: 12px; }}
  iframe {{ border: 1px solid #33322e; border-radius: 10px; background: #141413;
            display: block; box-shadow: 0 8px 24px rgba(0,0,0,.5); }}
</style></head>
<body>
<h1>Item Hunter UI</h1>
<p class="lede">Five artboards in a warm dark palette. This is a flat preview; the pan and zoom canvas needs Node.</p>
{note_html}
{''.join(blocks)}
</body></html>
"""
    dest = os.path.join(HERE, "preview.html")
    open(dest, "w", encoding="utf-8").write(out)
    print(f"wrote {dest} ({len(out)/1024:.0f} KB, {len(boards)} artboards)")


if __name__ == "__main__":
    main()
