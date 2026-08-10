#!/usr/bin/env python3
"""
Comprehensive metadata merge from embedded EXIF/XMP (Windows "Details" tab).

Windows "Details" fields -> EXIF/XMP tags:
  Title     -> XPTitle / dc:Title
  Subject   -> XPSubject / dc:Description
  Copyright -> Copyright / dc:Rights   (richest citation: author + work + institution + dates)
  Authors   -> Artist / dc:Creator
  Tags      -> XPKeywords / dc:Subject
  Comments  -> XPComment
  Date taken-> DateTimeOriginal / CreateDate

We capture EVERYTHING into a `file_metadata` block (lossless), then derive the
canonical display fields with clear provenance-aware precedence:
  - source/credit: Copyright > XPSubject-institution > existing real citation
  - date: image date from XPSubject year ONLY; copyright "First published" is the
           ARTICLE date, stored separately, never used as the image date
  - license: never guessed; if missing we flag it, do not invent one

Adds `needs_review` + `missing_fields` for images missing crucial use-data that
the file cannot supply. Writes a follow-up CSV of flagged images.

Safety: timestamped backup, </script> escaping, node --check, array/feedback asserts.
Run with --dry-run to preview.
"""
import json, time, sys, re, csv, subprocess, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(ROOT, "index.html")
FULL = os.path.join(ROOT, "drive_full_metadata_cache.json")
REPORT = os.path.join(ROOT, "metadata_followup.csv")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from parse_provenance import parse, is_real_institution

def find_array(html, name):
    marker = f"const {name} = "
    mi = html.find(marker)
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

def get(rec, *keys):
    for k in keys:
        if k in rec and rec[k]:
            return rec[k]
    return ""

def parse_copyright(text):
    """Extract author, work title, institution, publish/modify dates from a
    Windows Copyright / dc:Rights string."""
    out = {"author": "", "work": "", "institution": "", "published": "", "modified": ""}
    if not text:
        return out
    t = text.strip()
    # author: leading "Last, First." before first quote or first sentence end
    m = re.match(r'^([A-Z][\w.\'-]+(?:,[ \w.\'-]+)?)\.\s*', t)
    if m:
        out["author"] = m.group(1).strip()
    # work title in quotes
    mq = re.search(r'"([^"]+)"', t)
    if mq:
        out["work"] = mq.group(1)
    # published / modified dates
    mp = re.search(r'first published\s+([A-Za-z]+ \d{1,2}, \d{4})', t, re.I)
    if mp:
        out["published"] = mp.group(1)
    mm = re.search(r'last modified\s+([A-Za-z]+ \d{1,2}, \d{4})', t, re.I)
    if mm:
        out["modified"] = mm.group(1)
    # institution: text after the work quote, before 'First published'/'last modified'
    tail = t
    if mq:
        tail = t[mq.end():]
    tail = re.split(r'first published|last modified', tail, flags=re.I)[0]
    # strip leading period/comma, trailing period
    inst = tail.strip().lstrip(".").strip().rstrip(".").strip()
    if inst:
        out["institution"] = inst
    return out

def main():
    dry = "--dry-run" in sys.argv
    full = json.load(open(FULL))
    by_name = {r["_name"]: r for r in full}
    raw = open(HTML, encoding="utf-8").read()
    exh = find_array(raw, "EXHIBITS")
    alli = find_array(raw, "ALL_IMAGES")
    ad = json.loads(raw[alli[0]:alli[1]])
    imgs = []
    collect(ad, imgs)

    flagged = []
    applied = 0
    for o in imgs:
        fn = o.get("filename", "")
        rec = by_name.get(fn)
        if not rec:
            continue
        # ---- build lossless file_metadata block ----
        fm = {}
        title = get(rec, "IFD0:XPTitle", "XMP-dc:Title")
        subject = get(rec, "IFD0:XPSubject", "XMP-dc:Description", "IFD0:ImageDescription")
        copyright = get(rec, "IFD0:Copyright", "XMP-dc:Rights", "XMP-tiff:Copyright")
        authors = get(rec, "IFD0:Artist", "XMP-dc:Creator")
        tags = get(rec, "IFD0:XPKeywords", "XMP-dc:Subject")
        comment = get(rec, "IFD0:XPComment")
        date_taken = get(rec, "ExifIFD:DateTimeOriginal", "XMP-exif:DateTimeOriginal",
                         "Composite:DateTimeOriginal", "XMP-xmp:CreateDate")
        make = get(rec, "IFD0:Make"); model = get(rec, "IFD0:Model")
        usage = get(rec, "XMP-xmpRights:UsageTerms")
        if title: fm["title"] = title
        if subject: fm["subject"] = subject
        if copyright: fm["copyright"] = copyright
        if authors:
            fm["authors"] = authors if isinstance(authors, str) else ", ".join(authors)
        if tags:
            fm["tags"] = tags if isinstance(tags, str) else ", ".join(tags)
        if comment: fm["comment"] = comment
        if date_taken: fm["date_taken"] = str(date_taken)
        if make: fm["camera_make"] = make
        if model: fm["camera_model"] = model
        if usage: fm["usage_terms"] = usage

        cp = parse_copyright(copyright) if copyright else {}
        if cp.get("author"): fm["copyright_author"] = cp["author"]
        if cp.get("work"): fm["copyright_work"] = cp["work"]
        if cp.get("institution"): fm["copyright_institution"] = cp["institution"]
        if cp.get("published"): fm["copyright_published"] = cp["published"]
        if cp.get("modified"): fm["copyright_modified"] = cp["modified"]

        if not fm:
            continue
        o["file_metadata"] = fm
        applied += 1

        # ---- derive canonical source/credit (provenance-aware) ----
        cur = (o.get("source") or "").strip()
        # Copyright is the richest citation
        if cp.get("institution") or cp.get("author"):
            parts = []
            if cp.get("author"): parts.append(cp["author"])
            if cp.get("institution"): parts.append(cp["institution"])
            cite = ". ".join(parts) + " (from file copyright metadata)"
            # upgrade if current is vague/empty, or was a prior subject-derived placeholder
            if (cur in ("", "Archival source (unverified)") or cur.startswith("Archival source")
                    or "from archival file metadata" in cur):
                o["source"] = cite
            if cp.get("author"):
                o["credit"] = cp["author"]
        elif copyright:
            # copyright present but didn't parse to author/institution
            # (e.g. "Provincial Archives of Saskatchewan, R-A76, ...") -> use as-is
            cite = copyright.strip() + " (from file copyright metadata)"
            if (cur in ("", "Archival source (unverified)") or cur.startswith("Archival source")
                    or "from archival file metadata" in cur):
                o["source"] = cite
        elif subject:
            p = parse(subject)
            if cur in ("", "Archival source (unverified)") and is_real_institution(p["institution"]):
                o["source"] = f"{p['institution']} (from archival file metadata)"

        # ---- date: image date from subject year only; never article date ----
        if not (o.get("date") or "").strip() and subject:
            p = parse(subject)
            if p["date"]:
                o["date"] = p["date"]

        # ---- completeness / follow-up flag ----
        missing = []
        s = (o.get("source") or "").strip()
        if (not s) or s.startswith("Archival source") or s == "Unknown":
            missing.append("source")
        lic = (o.get("license") or "").strip()
        if (not lic) or lic == "Unknown":
            missing.append("license")
        if not (o.get("date") or "").strip():
            missing.append("date")
        # crucial-data check: is the gap recoverable from the file?
        recoverable = bool(copyright or subject)
        o["missing_fields"] = missing
        o["needs_review"] = bool(missing) and not recoverable
        # also flag if license missing even when source known (rights unclear)
        if "license" in missing:
            o["needs_review"] = True
        if dry:
            tag = "REVIEW" if o["needs_review"] else ("gap-filled" if missing else "ok")
            print(f"{fn}: {tag} | missing={missing} | src='{o.get('source','')[:45]}'")
        if o.get("needs_review") or missing:
            flagged.append({
                "filename": fn,
                "needs_review": o["needs_review"],
                "missing_fields": ", ".join(missing),
                "has_embedded_metadata": recoverable,
                "current_source": s,
                "copyright": (copyright or "")[:120],
                "subject": (subject or "")[:120],
            })

    print(f"\nadded file_metadata to {applied} images" + (" [dry-run]" if dry else ""))
    print(f"flagged for follow-up: {len(flagged)}")
    if dry:
        return

    new_alli = json.dumps(ad, ensure_ascii=False, separators=(",", ":"))
    new_html = raw[:alli[0]] + new_alli + raw[alli[1]:]
    last = new_html.rfind("</script>")
    new_html = new_html[:last].replace("</script>", "<\\/script>") + new_html[last:]
    assert new_html.count("<\\/script>") == 2, new_html.count("<\\/script>")
    assert new_html.count("</script>") == 1, new_html.count("</script>")
    ne = find_array(new_html, "EXHIBITS"); na = find_array(new_html, "ALL_IMAGES")
    assert new_html.count("const EXHIBITS = ") == 1 and new_html.count("const ALL_IMAGES = ") == 1
    json.loads(new_html[ne[0]:ne[1]]); json.loads(new_html[na[0]:na[1]])
    assert "let feedback = {}" in new_html
    bak = HTML + ".bak." + time.strftime("%Y%m%d-%H%M%S")
    open(bak, "w", encoding="utf-8").write(raw)
    open(HTML, "w", encoding="utf-8").write(new_html)
    # follow-up CSV
    with open(REPORT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["filename", "needs_review", "missing_fields",
                                          "has_embedded_metadata", "current_source",
                                          "copyright", "subject"])
        w.writeheader(); w.writerows(flagged)
    # node --check
    start = new_html.find("<script>") + len("<script>"); end = new_html.rfind("</script>")
    open("/tmp/full_main.js", "w", encoding="utf-8").write(new_html[start:end])
    rc = subprocess.run(["node", "--check", "/tmp/full_main.js"]).returncode
    assert rc == 0, "node --check failed"
    print(f"wrote index.html (backup {bak}); node OK; {applied} enriched; {len(flagged)} flagged -> {REPORT}")

if __name__ == "__main__":
    main()
