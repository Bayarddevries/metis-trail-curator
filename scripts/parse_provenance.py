#!/usr/bin/env python3
"""
Parse an embedded XPSubject provenance string into structured fields.

Expected shapes (real examples seen in the Drive folder):
  [Red River carts loading at trading house, St. Paul] [Photograph]. (1854). MNopedia, Minnesota Historical Society.
  Upton, B. F. (1860). [Metis drivers with Red River ox carts, probably in Minnesota] [Photograph] ...
  Preparing a Red River cart train at Pembina for a trip to St. Anthony Falls. 1856. Minnesota Historical Society.
  Whitney's Gallery. (1862-1875). Red River carts [Stereograph]. MNopedia, New York Public Library Digital Collections. https://digitalcollections.nypl.org/items/...

Strategy (defensive, never throws):
  - date: first 4-digit year, or "ca. YYYY", or range "YYYY-YYYY" / "YYYY-YY"
  - url: first http(s) substring
  - institution: the trailing "... . <Institution> [URL]" segment (everything after the last ". " that isn't the date)
  - We keep the FULL raw subject in `provenance` regardless, so nothing is lost.
"""
import re, json, sys

# A plausible historical year: 1500-1999. Filters out IDs like "R-A8587", "2440 Broad Street", "5874".
YEAR_RE = re.compile(r"(?:\(|\[|\s)((?:ca\.?\s*)?((?:18|19)\d{2}))(?:\s*[-–]\s*(\d{2,4}))?(?:\)|\]|\s|\.|$)")
URL_RE = re.compile(r"https?://\S+", re.I)

def _clean_year(y):
    return y.replace("ca.", "").strip()

def parse(subject):
    subject = (subject or "").strip()
    out = {"provenance": subject, "date": "", "url": "", "institution": ""}
    if not subject:
        return out
    # URL
    m = URL_RE.search(subject)
    if m:
        out["url"] = m.group(0).rstrip(").,")
    # DATE: first plausible historical year (1500-1999), with optional range end
    best = None
    for dm in YEAR_RE.finditer(subject):
        yr = _clean_year(dm.group(2))
        end = dm.group(3)
        if end:
            if len(end) == 2:
                end = yr[:2] + end
            cand = f"{yr}-{end}"
        else:
            cand = yr
        best = cand
        break  # first plausible year wins
    if best:
        out["date"] = best
    # INSTITUTION: for the archive-citation shape "... (YYYY). <Institution>. <URL>"
    # the institution is the segment after the date+period, before any URL.
    inst = ""
    m2 = re.search(r"(?:\)|\]|\d{4})\.\s*([^h].*?)(?:\.\s*https?://|$)", subject)
    if m2:
        inst = m2.group(1)
    # fallback: text after last " . " if it looks like an institution
    if not inst:
        parts = [p.strip() for p in subject.split(". ")]
        for p in reversed(parts):
            if p and not re.search(r"\b(?:18|19)\d{2}\b", p) and "http" not in p:
                inst = p
                break
    inst = inst.strip().strip(".").strip()
    if out["url"] and out["url"] in inst:
        inst = inst.replace(out["url"], "").strip().rstrip(",").strip()
    # strip a leading leftover bracketed type label like "[Photograph]. " or "(1854). "
    inst = re.sub(r"^(\[?[A-Za-z /]+\]?|\([^)]*\))\.\s*", "", inst)
    out["institution"] = inst
    return out

if __name__ == "__main__":
    cache = json.load(open("drive_exif_cache.json"))
    rows = [r for r in cache if r["xpsubject"]]
    for r in rows[:int(sys.argv[1]) if len(sys.argv) > 1 else len(rows)]:
        p = parse(r["xpsubject"])
        print("FILE:", r["name"])
        print("  raw :", r["xpsubject"][:130])
        print("  -> date:", p["date"], "| url:", p["url"][:60], "| inst:", p["institution"][:70])
