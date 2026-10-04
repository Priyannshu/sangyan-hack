"""Build a styled PDF from docs/tech-stack-workflow.md via markdown -> HTML -> Chrome."""
import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
MD = ROOT / "docs" / "tech-stack-workflow.md"
HTML = ROOT / "docs" / "_tech-stack-workflow.html"
PDF = ROOT / "docs" / "tech-stack-workflow.pdf"

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body {
  font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.55; color: #1a1a1a; margin: 0;
}
h1 { font-size: 21pt; margin: 0 0 .2em; color: #0b1f3a; letter-spacing: -.01em; }
h2 {
  font-size: 14pt; margin: 1.6em 0 .5em; padding-bottom: .25em;
  border-bottom: 2px solid #0b1f3a; color: #0b1f3a;
}
h3 { font-size: 11.5pt; margin: 1.2em 0 .35em; color: #123; }
p { margin: .5em 0; }
a { color: #0b5cad; text-decoration: none; }
strong { color: #0b1f3a; }
hr { border: 0; border-top: 1px solid #d8dee8; margin: 1.6em 0; }
blockquote {
  margin: 1em 0; padding: .6em 1em; border-left: 3px solid #0b5cad;
  background: #f4f8fd; color: #33475e; font-style: italic;
}
code {
  font-family: "Cascadia Code", Consolas, "Courier New", monospace;
  font-size: 9pt; background: #eef2f7; padding: .1em .35em; border-radius: 3px;
}
pre {
  background: #0d1b2a; color: #e6edf3; padding: .8em 1em; border-radius: 6px;
  overflow-x: auto; font-size: 9pt; line-height: 1.45;
}
pre code { background: none; color: inherit; padding: 0; }
table {
  border-collapse: collapse; width: 100%; margin: .9em 0; font-size: 9.5pt;
}
th, td { border: 1px solid #cdd6e2; padding: .42em .6em; text-align: left; vertical-align: top; }
th { background: #0b1f3a; color: #fff; font-weight: 600; }
tr:nth-child(even) td { background: #f6f9fc; }
ul, ol { margin: .5em 0 .5em 1.2em; padding-left: 1em; }
li { margin: .25em 0; }
h2, h3 { break-after: avoid; }
table, pre, blockquote { break-inside: avoid; }
"""


def main() -> int:
    text = MD.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])
    HTML.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Borrowed Badge — Tech Stack &amp; Workflow</title>"
        f"<style>{CSS}</style></head><body>{body}</body></html>",
        encoding="utf-8",
    )

    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not chrome.exists():
        chrome = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

    cmd = [
        str(chrome), "--headless", "--disable-gpu", "--no-pdf-header-footer",
        f"--print-to-pdf={PDF}", HTML.as_uri(),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)

    if not PDF.exists() or PDF.stat().st_size == 0:
        print("PDF was not produced", file=sys.stderr)
        return 1
    HTML.unlink(missing_ok=True)
    print(f"OK {PDF} ({PDF.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
