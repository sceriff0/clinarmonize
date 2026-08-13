"""Extract step cards from the schematic into per-step Markdown briefs.

§0.2 tells the implementing agent to read §0 and one step card, and nothing
else.  That is only enforceable if a card exists as a standalone file, so this
script is what makes the build protocol mechanical rather than aspirational.
"""
import html
import re
import sys
from pathlib import Path

SLOT_TITLES = {
    "why": "Why", "io": "I/O", "contract": "Contract", "params": "Params",
    "alts": "Alternatives", "trap": "Trap", "nogo": "Not here (nogo)",
    "docs": "Docs", "done": "Done when",
}
# Slots whose content is code or a table stay verbatim; prose slots get unwrapped.
CODE_SLOTS = {"io", "contract", "done"}


def text_of(fragment: str) -> str:
    fragment = re.sub(r"<span class=\"lbl\">.*?</span>", "", fragment, flags=re.S)
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"</(p|li|div)>", "\n\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return html.unescape(fragment)


def table_to_markdown(fragment: str) -> str:
    rows = []
    for row_html in re.findall(r"<tr>(.*?)</tr>", fragment, flags=re.S):
        cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row_html, flags=re.S)
        rows.append([" ".join(text_of(c).split()) or "—" for c in cells])
    if not rows:
        return ""
    out = ["| " + " | ".join(rows[0]) + " |",
           "|" + "|".join(["---"] * len(rows[0])) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def render_slot(slot: str, fragment: str) -> str:
    body = []
    # Pull code blocks out first so table/prose handling never sees them.
    for code in re.findall(r"<pre><code>(.*?)</code></pre>", fragment, flags=re.S):
        body.append("```\n" + text_of(code).strip() + "\n```")
    fragment = re.sub(r"<pre><code>.*?</code></pre>", "", fragment, flags=re.S)
    for table in re.findall(r"<table>.*?</table>", fragment, flags=re.S):
        body.append(table_to_markdown(table))
    fragment = re.sub(r"<table>.*?</table>", "", fragment, flags=re.S)
    if slot == "docs":
        for href, label in re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', fragment, flags=re.S):
            body.append(f"- [{html.unescape(label).strip()}]({href})")
    else:
        prose = "\n".join(" ".join(p.split()) for p in text_of(fragment).split("\n\n"))
        prose = "\n".join(line for line in prose.splitlines() if line.strip())
        if prose.strip():
            body.insert(0 if slot not in CODE_SLOTS else len(body), prose.strip())
    return "\n\n".join(b for b in body if b.strip())


def main(src: Path, outdir: Path) -> None:
    doc = src.read_text(encoding="utf-8")
    outdir.mkdir(parents=True, exist_ok=True)
    written = 0
    for card in re.findall(r'<li class="step" id="s[\d-]+">.*?\n    </li>', doc, flags=re.S):
        num, title = re.search(
            r'<h3><span class="n">§([\d.]+)</span><span>(.*?)</span></h3>', card, flags=re.S
        ).groups()
        lines = [f"# §{num} — {html.unescape(title).strip()}", "",
                 "> Extracted verbatim from `docs/schematics/p-harmonize.html`.",
                 "> Per §0.2: read §0 and this card. Read nothing else.", ""]
        for slot, heading in SLOT_TITLES.items():
            match = re.search(
                rf'<div class="slot" data-slot="{slot}">(.*?)</div>\s*(?=<div class="slot"|</li>)',
                card, flags=re.S)
            if not match:
                continue
            rendered = render_slot(slot, match.group(1))
            if rendered:
                lines += [f"## {heading}", "", rendered, ""]
        (outdir / f"s{num.replace('.', '-')}.md").write_text("\n".join(lines), encoding="utf-8")
        written += 1
    print(f"wrote {written} step cards to {outdir}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
