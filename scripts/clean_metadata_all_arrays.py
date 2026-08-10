#!/usr/bin/env python3
"""
Clean metadata across ALL top-level const arrays in index.html (EXHIBITS + ALL_IMAGES)
against the Wikimedia Commons API.

The deployed dashboard has two large JSON arrays embedded in index.html:
  - const EXHIBITS = [...]   (the Exhibits tab; nested image objects with a `source` field)
  - const ALL_IMAGES = [...] (the Images tab; top-level objects with a `source` field)
Both contained garbage in `source`:
  - "Wikimedia Commons (metadata unavailable)"  (scrape couldn't read Commons meta)
  - "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#" and similar XMP namespace URIs
  - "Wikimedia Commons ()"  (empty credit fallback)
and a few entries had garbled titles (binary JPEG header leaked during conversion).

This resolves all of them against the Commons API. Unresolvable entries (some
filenames are local derivatives whose original Commons title is lost) get a clean
generic "Wikimedia Commons" source so the page never shows the broken values again.
Idempotent: re-running only touches entries still broken. One timestamped .bak backup.

Usage:
  uv run python3 scripts/clean_metadata_all_arrays.py [--dry-run]
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML = os.path.join(REPO, "index.html")
API = "https://commons.wikimedia.org/w/api.php"
UA = "RRMHC-ExhibitReview/1.0 (github.com/Bayarddevries/red-river-metis-exhibit-review)"

_dry = "--dry-run" in sys.argv


def clean_html(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&#160;", " ").replace("&nbsp;", " ")
    s = re.sub(r"\{\{[^}]*\}\}", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_garbage_source(s):
    s = (s or "").strip()
    if not s:
        return True
    low = s.lower()
    if "metadata unavailable" in low:
        return True
    if "ns.adobe.com" in low or "ns.example.com" in low:
        return True
    if s == "Wikimedia Commons ()":
        return True
    return False


def is_garbled_title(t):
    return "%&'" in (t or "")


def commons_meta(filename):
    title = "File:" + filename
    q = urllib.parse.urlencode({
        "action": "query",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "extmetadata|url",
        "format": "json",
    })
    url = API + "?" + q
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
            pages = data.get("query", {}).get("pages", {})
            for _, v in pages.items():
                if v.get("missing") is not None:
                    return None
                ii = (v.get("imageinfo") or [{}])[0]
                em = ii.get("extmetadata", {})
                desc = clean_html(em.get("ImageDescription", {}).get("value", ""))
                artist = clean_html(em.get("Artist", {}).get("value", ""))
                credit = clean_html(em.get("Credit", {}).get("value", ""))
                if not credit:
                    credit = clean_html(em.get("ObjectName", {}).get("value", ""))
                if not credit and artist:
                    credit = artist
                lic = clean_html(em.get("LicenseShortName", {}).get("value", ""))
                date = clean_html(em.get("DateTimeOriginal", {}).get("value", ""))
                if not date:
                    date = clean_html(em.get("Date", {}).get("value", ""))
                page = "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(title)
                return {
                    "desc": desc,
                    "artist": artist,
                    "credit": credit,
                    "license": lic,
                    "date": date,
                    "page": page,
                }
            return None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt * 5)
                continue
            return None
        except Exception:
            return None
    return None


def find_array(html, name):
    """Return (start_of_value, end_of_value_exclusive) for `const NAME = [ ... ]`."""
    marker = f"const {name} = "
    mi = html.find(marker)
    if mi == -1:
        return None
    start = mi + len(marker)  # points at '['
    # closing ']' is the last ']' before the next top-level array's marker or 'let feedback'
    sentinel = ";\n\nlet feedback"
    si = html.find(sentinel, mi)
    if si == -1:
        si = len(html)
    # restrict search to between this array's start and the next 'const ' marker or sentinel
    next_const = html.find("const ", start)
    upper = min(x for x in (next_const, si) if x != -1)
    end = html.rfind("]", start, upper) + 1
    return start, end


def collect_garbage(objs_with_source):
    """objs_with_source: list of dicts that have a 'source' field.
    Returns (to_resolve: dict filename->meta, stats counters mutable)."""
    return {}


def main():
    raw = open(HTML, encoding="utf-8").read()

    # Locate both arrays
    exh = find_array(raw, "EXHIBITS")
    alli = find_array(raw, "ALL_IMAGES")
    if not exh or not alli:
        raise RuntimeError("could not locate EXHIBITS and/or ALL_IMAGES arrays")
    exh_data = json.loads(raw[exh[0]:exh[1]])
    all_data = json.loads(raw[alli[0]:alli[1]])

    # Walk both trees, find every dict with a garbage 'source' and a 'filename'
    work = []  # list of (dict_ref_from_parsed_tree, which_array)
    def walk(o):
        if isinstance(o, dict):
            if "source" in o and is_garbage_source(o.get("source")) and o.get("filename"):
                work.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(exh_data)
    walk(all_data)

    # Unique filenames to resolve
    unique = {}
    for d in work:
        unique.setdefault(d["filename"], None)

    print(f"EXHIBITS entries: {len(exh_data)} | ALL_IMAGES entries: {len(all_data)}")
    print(f"garbage 'source' objects found: {len(work)} across {len(unique)} unique filenames")

    # Resolve each unique filename once (with a small cache)
    cache = {}
    fixed_unavail = fixed_xmp = fixed_other = fixed_garbled = 0
    filled_date = filled_license = 0
    unresolved = 0

    for fn in unique:
        meta = commons_meta(fn)
        cache[fn] = meta
        time.sleep(0.5)

    for d in work:
        fn = d["filename"]
        meta = cache.get(fn)
        was_unavail = "metadata unavailable" in (d.get("source") or "").lower()
        was_xmp = "ns.adobe.com" in (d.get("source") or "").lower() or "ns.example.com" in (d.get("source") or "").lower()
        was_garbled = is_garbled_title(d.get("title"))

        if was_garbled and meta and meta.get("desc"):
            d["title"] = meta["desc"][:200]
            fixed_garbled += 1

        if is_garbage_source(d.get("source")):
            if meta and meta.get("credit"):
                d["source"] = f"Wikimedia Commons — {meta['credit']}"
            elif meta:
                d["source"] = f"Wikimedia Commons ({meta['page']})"
            else:
                d["source"] = "Wikimedia Commons"
                unresolved += 1
            if was_unavail:
                fixed_unavail += 1
            elif was_xmp:
                fixed_xmp += 1
            else:
                fixed_other += 1

        if not d.get("date") and meta and meta.get("date"):
            d["date"] = meta["date"]
            filled_date += 1
        if (not d.get("license") or d.get("license") in ("Unknown", "")) and meta and meta.get("license"):
            d["license"] = meta["license"]
            filled_license += 1

    print(f"fixed 'metadata unavailable' sources : {fixed_unavail}")
    print(f"fixed xmp/adobe namespace sources    : {fixed_xmp}")
    print(f"fixed other garbage sources         : {fixed_other}")
    print(f"fixed garbled titles                 : {fixed_garbled}")
    print(f"filled missing dates                : {filled_date}")
    print(f"filled missing licenses             : {filled_license}")
    print(f"unresolved (Commons 404 / no meta)  : {unresolved} -> set to generic 'Wikimedia Commons'")

    if _dry:
        print("[dry-run] not writing index.html")
        return

    # Re-emit, preserving the exact surrounding structure.
    new_exh = json.dumps(exh_data, ensure_ascii=False, separators=(",", ":")).replace("</script", "<\\/script")
    new_all = json.dumps(all_data, ensure_ascii=False, separators=(",", ":")).replace("</script", "<\\/script")

    # raw layout: ... [EXHARR] ]; \n const ALL_IMAGES = [ALLARR] ]; \n\nlet feedback...
    # exh[0]=start of EXHARR, exh[1]=end of EXHARR (the ']')
    # alli[0]=start of ALLARR, alli[1]=end of ALLARR
    new_html = (
        raw[:exh[0]]
        + new_exh
        + raw[exh[1]:alli[0]]
        + new_all
        + raw[alli[1]:]
    )

    # integrity assertions: re-locate array boundaries in the NEW html
    # (the JSON was compacted, so the original raw offsets no longer apply)
    def find_in(html, name):
        marker = f"const {name} = "
        mi = html.find(marker)
        start = mi + len(marker)
        sentinel = ";\n\nlet feedback"
        si = html.find(sentinel, mi)
        if si == -1:
            si = len(html)
        next_const = html.find("const ", start)
        upper = min(x for x in (next_const, si) if x != -1)
        end = html.rfind("]", start, upper) + 1
        return start, end

    ne = find_in(new_html, "EXHIBITS")
    na = find_in(new_html, "ALL_IMAGES")
    assert new_html.count("const EXHIBITS = ") == 1, "EXHIBITS marker duplicated!"
    assert new_html.count("const ALL_IMAGES = ") == 1, "ALL_IMAGES marker duplicated!"
    json.loads(new_html[ne[0]:ne[1]])
    json.loads(new_html[na[0]:na[1]])
    assert "let feedback = {}" in new_html, "feedback declaration lost!"

    bak = HTML + ".bak." + time.strftime("%Y%m%d-%H%M%S")
    open(bak, "w", encoding="utf-8").write(raw)
    open(HTML, "w", encoding="utf-8").write(new_html)
    print(f"wrote index.html (backup: {os.path.basename(bak)})")


if __name__ == "__main__":
    main()
