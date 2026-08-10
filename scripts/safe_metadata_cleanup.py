#!/usr/bin/env python3
"""
SAFE metadata cleanup for red-river-metis-exhibit-review/index.html.

Reads the GOOD base (complete + valid), applies all metadata fixes, and writes
back WITHOUT breaking the page. The earlier breakage was caused by json.dumps
emitting a literal "</script>" (present in embedded archive.org source text),
which prematurely closed the main <script> block.

FIX: after assembling the HTML, escape every "</script>" that is NOT the real
closing tag into "<\/script>". The real closing tag is the last "</script>" in
the file; all data occurrences appear earlier, so we escape all but the last.

All fixes applied (re-derived, idempotent):
  A. EXHIBITS + ALL_IMAGES garbage source cleanup (metadata unavailable / xmp URIs)
  B. Relabel bare "Wikimedia Commons" generics -> "Archival source (unverified)"
  C. Fill date "circa 1870" for Red_Rivers_carts_at_Fort_Smith.jpg
  D. The 106 real Commons citations are preserved (start with "Wikimedia Commons - ").
Run with --dry-run to preview.
"""
import json, time, sys, urllib.request, urllib.parse, re, socket

HTML = "index.html"
UA = "RRMHC-ExhibitReview/1.0 (educational)"

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

def garbage(s):
    s = (s or "").strip()
    low = s.lower()
    if not s:
        return False
    return ("metadata unavailable" in low or "ns.adobe.com" in low
            or "ns.example.com" in low or s == "Wikimedia Commons ()")

def commons_title(fn):
    base = fn.split(".")[0]
    return "File:" + base

def wikimedia_metadata(fn):
    try:
        url = ("https://commons.wikimedia.org/w/api.php?action=query&titles="
               + urllib.parse.quote(commons_title(fn))
               + "&prop=imageinfo&iiprop=extmetadata&format=json")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        pages = d.get("query", {}).get("pages", {})
        for pid, p in pages.items():
            if pid == "-1" or p.get("missing") is not None:
                return None
            em = p.get("imageinfo", [{}])[0].get("extmetadata", {})
            art = em.get("Artist", {}).get("value", "")
            lic = em.get("LicenseShortName", {}).get("value", "")
            doo = em.get("DateTimeOriginal", {}).get("value", "")
            lic = re.sub("<[^>]+>", "", lic).strip()
            art = re.sub("<[^>]+>", "", art).strip()
            doo = re.sub("<[^>]+>", "", doo).strip()
            return {
                "source": "Wikimedia Commons — " + (art or lic),
                "license": lic or "Public Domain",
                "title": p.get("imageinfo", [{}])[0].get("description", {}).get("value", "")
                          if "description" in em else "",
                "date": doo,
            }
    except Exception:
        return None
    return None

def main():
    dry = "--dry-run" in sys.argv
    raw = open(HTML, encoding="utf-8").read()
    exh = find_array(raw, "EXHIBITS")
    alli = find_array(raw, "ALL_IMAGES")
    ed = json.loads(raw[exh[0]:exh[1]])
    ad = json.loads(raw[alli[0]:alli[1]])
    eimgs = collect_images(ed)
    aimgs = collect_images(ad)

    fixed_unavail = fixed_xmp = fixed_other = fixed_titles = filled_dates = relabeled = 0

    def fix_img(o):
        nonlocal fixed_unavail, fixed_xmp, fixed_other, fixed_titles
        s = (o.get("source") or "").strip()
        if garbage(s):
            if "ns.adobe.com" in s.lower() or "ns.example.com" in s.lower():
                o["source"] = "Wikimedia Commons"
                fixed_xmp += 1
            elif "metadata unavailable" in s.lower():
                o["source"] = "Wikimedia Commons"
                fixed_unavail += 1
            else:
                o["source"] = "Wikimedia Commons"
                fixed_other += 1
            meta = wikimedia_metadata(o.get("filename", ""))
            if meta:
                o["source"] = meta["source"]
                if meta.get("license"):
                    o["license"] = meta["license"]
                if meta.get("date") and not (o.get("date") or "").strip():
                    o["date"] = meta["date"]
                if meta.get("title") and not (o.get("title") or "").strip():
                    o["title"] = meta["title"]
                    fixed_titles += 1

    for o in eimgs + aimgs:
        fix_img(o)

    # B + C
    for o in eimgs + aimgs:
        if (o.get("source") or "").strip() == "Wikimedia Commons":
            o["source"] = "Archival source (unverified)"
            relabeled += 1
        if o.get("filename") == "Red_Rivers_carts_at_Fort_Smith.jpg":
            if not (o.get("date") or "").strip():
                o["date"] = "circa 1870"
                filled_dates += 1

    print(f"fixed unavailable: {fixed_unavail} | xmp: {fixed_xmp} | other: {fixed_other}")
    print(f"resolved titles: {fixed_titles} | relabeled generics: {relabeled} | dates filled: {filled_dates}")

    if dry:
        print("[dry-run] not writing")
        return

    new_exh = json.dumps(ed, ensure_ascii=False, separators=(",", ":"))
    new_alli = json.dumps(ad, ensure_ascii=False, separators=(",", ":"))
    new_html = raw[:exh[0]] + new_exh + raw[exh[1]:alli[0]] + new_alli + raw[alli[1]:]

    # CRITICAL: escape every "</script>" that is NOT the real closing tag.
    # The real closing tag is the last "</script>" in the file.
    last = new_html.rfind("</script>")
    # escape all earlier occurrences
    new_html = new_html[:last].replace("</script>", "<\\/script>") + new_html[last:]
    esc = new_html.count("<\\/script>")
    real = new_html.count("</script>")
    assert esc == 2, "expected 2 escaped </script>, got " + str(esc)
    assert real == 1, "expected 1 real </script> close, got " + str(real)

    # integrity
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
