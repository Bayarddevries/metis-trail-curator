#!/usr/bin/env python3
"""
Merge extracted Drive EXIF provenance (XPSubject) into the exhibit review
index.html ALL_IMAGES array.

For each of the 51 downloaded Drive images that matches an ALL_IMAGES entry
by filename:
  - store raw Subject in a new `provenance` field (never loses data)
  - if current source is the vague "Archival source (unverified)", upgrade it
    to the parsed institution (e.g. "Minnesota Historical Society — via MNopedia")
  - if `date` is empty, fill it from the parsed date
  - if the Subject carries a real source URL and current `url` is empty/weak,
    set `url` to it

Safety:
  - timestamped backup before write
  - escape every "</script>" except the real closing tag (the bug that broke the page)
  - node --check on the resulting inline script
  - assert both arrays parse and `let feedback` survives
Run with --dry-run to preview changes.
"""
import json, time, sys, re, subprocess, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "index.html")
EXIF = os.path.join(ROOT, "drive_exif_cache.json")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from parse_provenance import parse

def find_array(html, name):
    marker = f"const {name} = "
    mi = html.find(marker)
    if mi == -1:
        raise SystemExit(f"marker {name} not found")
    start = mi + len(marker)
    si = html.find(";\n\nlet feedback", mi)
    si = -1 if si == -1 else si
    nc = html.find("const ", start)
    upper = min(x for x in (nc, si) if x != -1)
    end = html.rfind("]", start, upper) + 1
    return start, end

def collect(o, out):
    if isinstance(o, dict):
        if "filename" in o and "source" in o:
            out.append(o)
        for v in o.values():
            collect(v, out)
    elif isinstance(o, list):
        for v in o:
            collect(v, out)

# Institutions worth citing as `source` (real holding bodies).
INST_KW = ["archives", "archive", "historical society", "library", "museum",
           "collection", "mnopedia", "nypl", "glenbow", "lac", "lac.", "saskatchewan",
           "manitoba", "canada", "congress", "digit", "wikipedia", "commons"]

def is_real_institution(s):
    s = (s or "").strip()
    if not s:
        return False
    low = s.lower()
    # reject sentence fragments / full descriptive paragraphs
    if len(s) > 70:
        return False
    if s.endswith((".", ",")):
        return False
    # reject anything that looks like an address or sentence (street number, verb-y)
    if re.search(r"\b\d{2,4}\s+(broad|street|avenue|ave|road|rd|lane|st)\b", low):
        return False
    if low.startswith(("the ", "a ", "an ", "he ", "she ", "they ", "it ", "this ", "photo", "image")):
        return False
    # must contain a recognizable institution keyword
    return any(k in low for k in INST_KW)

def main():
    dry = "--dry-run" in sys.argv
    exif = json.load(open(EXIF))
    by_name = {r["name"]: r for r in exif}
    raw = open(HTML, encoding="utf-8").read()

    exh = find_array(raw, "EXHIBITS")
    alli = find_array(raw, "ALL_IMAGES")
    ad = json.loads(raw[alli[0]:alli[1]])
    imgs = []
    collect(ad, imgs)

    applied = 0
    for o in imgs:
        fn = o.get("filename", "")
        r = by_name.get(fn)
        if not r:
            continue
        subj = r.get("xpsubject") or ""
        if not subj:
            continue
        p = parse(subj)
        o["provenance"] = subj  # always store raw
        changed = [f"provenance set"]
        # upgrade vague source ONLY when institution parses to a real body
        cur = (o.get("source") or "").strip()
        if cur == "Archival source (unverified)" and is_real_institution(p["institution"]):
            o["source"] = f"{p['institution']} (from archival file metadata)"
            changed.append("source->" + o["source"][:50])
        # fill date
        if not (o.get("date") or "").strip() and p["date"]:
            o["date"] = p["date"]
            changed.append("date->" + p["date"])
        # upgrade url if subject has a real one and current is empty/commons-ish
        cur_url = (o.get("url") or "").strip()
        if p["url"] and ("wikipedia.org/wiki/File:" in cur_url or not cur_url):
            o["url"] = p["url"]
            changed.append("url->" + p["url"][:40])
        applied += 1
        if dry:
            print(f"{fn}: " + "; ".join(changed) + f" | inst='{p['institution'][:40]}' date='{p['date']}'")

    print(f"\napplied to {applied} images" + (" [dry-run]" if dry else ""))
    if dry:
        return

    new_alli = json.dumps(ad, ensure_ascii=False, separators=(",", ":"))
    new_html = raw[:alli[0]] + new_alli + raw[alli[1]:]
    # CRITICAL escape: keep only the LAST real </script> as the close tag
    last = new_html.rfind("</script>")
    new_html = new_html[:last].replace("</script>", "<\\/script>") + new_html[last:]
    assert new_html.count("<\\/script>") == 2, new_html.count("<\\/script>")
    assert new_html.count("</script>") == 1, new_html.count("</script>")

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

    # node --check the inline script
    start = new_html.find("<script>") + len("<script>")
    end = new_html.rfind("</script>")
    open("/tmp/merged_main.js", "w", encoding="utf-8").write(new_html[start:end])
    rc = subprocess.run(["node", "--check", "/tmp/merged_main.js"]).returncode
    assert rc == 0, "node --check failed"
    print(f"wrote index.html (backup {bak}); node --check OK; provenance applied to {applied} images")

if __name__ == "__main__":
    main()
