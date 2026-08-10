#!/usr/bin/env python3
"""
Option 1 targeted completion for red-river-metis-exhibit-review/index.html.

Two precise, low-risk fixes (both approved by user):
  1. Relabel every image object whose source is the bare string "Wikimedia Commons"
     (113 entries, all confirmed NOT actually on Commons) to an honest
     "Archival source (unverified)" label. The 106 real Commons entries carry a
     "Wikimedia Commons - <title>" citation and are untouched.
  2. Fill the one recoverable date among URL-sourced entries:
     Red_Rivers_carts_at_Fort_Smith.jpg -> "circa 1870" (Commons metadata).

No scraping of external institutions (Glenbow/LAC), no mass rewrites.
Self-verifying: asserts both arrays re-parse and markers are unique before writing.
"""
import json, time, sys

HTML = "index.html"

def find_array(html, name):
    marker = f"const {name} = "
    mi = html.find(marker)
    if mi == -1:
        raise SystemExit(f"marker {name} not found")
    start = mi + len(marker)
    sentinel = ";\n\nlet feedback"
    si = html.find(sentinel, mi)
    if si == -1:
        si = len(html)
    next_const = html.find("const ", start)
    upper = min(x for x in (next_const, si) if x != -1)
    end = html.rfind("]", start, upper) + 1
    return start, end

def collect_images(objs):
    """Return list of image-like dicts (have both filename and source)."""
    out = []
    def walk(o):
        if isinstance(o, dict):
            if "filename" in o and "source" in o:
                out.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    for o in objs:
        walk(o)
    return out

def main():
    dry = "--dry-run" in sys.argv
    raw = open(HTML, encoding="utf-8").read()
    exh = find_array(raw, "EXHIBITS")
    alli = find_array(raw, "ALL_IMAGES")
    ed = json.loads(raw[exh[0]:exh[1]])
    ad = json.loads(raw[alli[0]:alli[1]])

    eimgs = collect_images(ed)
    aimgs = collect_images(ad)

    relabel_to = "Archival source (unverified)"
    relabeled = 0
    for o in eimgs + aimgs:
        if (o.get("source") or "").strip() == "Wikimedia Commons":
            o["source"] = relabel_to
            relabeled += 1

    filled_date = 0
    for o in eimgs + aimgs:
        if o.get("filename") == "Red_Rivers_carts_at_Fort_Smith.jpg":
            if not (o.get("date") or "").strip():
                o["date"] = "circa 1870"
                filled_date += 1

    print(f"entries relabeled 'Wikimedia Commons' -> '{relabel_to}': {relabeled}")
    print(f"dates filled: {filled_date}")

    if dry:
        print("[dry-run] not writing index.html")
        return

    new_exh = json.dumps(ed, ensure_ascii=False, separators=(",", ":"))
    new_alli = json.dumps(ad, ensure_ascii=False, separators=(",", ":"))

    new_html = (
        raw[:exh[0]] + new_exh + raw[exh[1]:alli[0]] + new_alli + raw[alli[1]:]
    )

    # integrity assertions in NEW html
    ne = find_array(new_html, "EXHIBITS")
    na = find_array(new_html, "ALL_IMAGES")
    assert new_html.count("const EXHIBITS = ") == 1
    assert new_html.count("const ALL_IMAGES = ") == 1
    json.loads(new_html[ne[0]:ne[1]])
    json.loads(new_html[na[0]:na[1]])
    assert "let feedback = {}" in new_html

    bak = HTML + ".bak." + time.strftime("%Y%m%d-%H%M%S")
    open(bak, "w", encoding="utf-8").write(raw)
    open(HTML, "w", encoding="utf-8").write(new_html)
    print(f"wrote index.html (backup: {bak})")

if __name__ == "__main__":
    main()
