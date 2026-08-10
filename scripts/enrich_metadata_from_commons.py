#!/usr/bin/env python3
"""
Clean + enrich image metadata in index.html against the Wikimedia Commons API.

The deployed dashboard's ALL_IMAGES array is the only surviving copy of the data
(the wiki manifest + build script that generated it are gone from this machine).
Many entries carry garbage in their `source` field:
  - "Wikimedia Commons (metadata unavailable)"  (scrape couldn't read Commons meta)
  - "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#" and similar XMP namespace
    URIs leaked from image EXIF/XMP into the source field
  - "Wikimedia Commons ()"  (empty credit fallback)
plus a few entries whose `title` is a binary JPEG header leaked during conversion.

This script resolves all of those against the Commons API. Unresolvable entries
(some filenames are local derivatives whose original Commons title is lost) get a
clean generic "Wikimedia Commons" source rather than the garbage string, so the
page never displays the broken values again. Idempotent: re-running only touches
entries still broken.

Usage:
  uv run python3 scripts/enrich_metadata_from_commons.py [--dry-run]
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
        return True  # treat empty-credit as garbage to normalize
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
            return None  # other HTTP errors -> treat as unresolved
        except Exception:
            return None
    return None


def extract_array(html):
    marker = "const ALL_IMAGES = "
    sentinel = ";\n\nlet feedback"
    mi = html.find(marker)
    si = html.find(sentinel, mi)
    if mi == -1 or si == -1:
        raise RuntimeError("could not locate ALL_IMAGES array / let feedback sentinel")
    start = mi + len(marker)
    # The array's closing ']' is right before the sentinel.
    arr_end = html.rfind("]", start, si) + 1
    return html, mi, start, arr_end


def main():
    raw = open(HTML, encoding="utf-8").read()
    html, mi, start, arr_end = extract_array(raw)
    data = json.loads(raw[start:arr_end])
    print(f"loaded {len(data)} image entries")

    fixed_unavail = fixed_xmp = fixed_other_garbage = 0
    fixed_garbled = filled_date = filled_license = 0
    unresolved = 0

    for d in data:
        fn = d.get("filename", "")
        needs_work = is_garbage_source(d.get("source")) or is_garbled_title(d.get("title"))
        if not needs_work:
            continue

        was_unavail = "metadata unavailable" in (d.get("source") or "").lower()
        was_xmp = "ns.adobe.com" in (d.get("source") or "").lower() or "ns.example.com" in (d.get("source") or "").lower()
        was_garbled = is_garbled_title(d.get("title"))

        meta = commons_meta(fn)

        # Title
        if was_garbled and meta and meta.get("desc"):
            d["title"] = meta["desc"][:200]
            fixed_garbled += 1

        # Source: always end up with a clean value
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
                fixed_other_garbage += 1

        # Fill missing date / license from Commons where available
        if not d.get("date") and meta and meta.get("date"):
            d["date"] = meta["date"]
            filled_date += 1
        if (not d.get("license") or d.get("license") in ("Unknown", "")) and meta and meta.get("license"):
            d["license"] = meta["license"]
            filled_license += 1

        time.sleep(0.5)  # be polite to Commons

    print(f"fixed 'metadata unavailable' sources : {fixed_unavail}")
    print(f"fixed xmp/adobe namespace sources    : {fixed_xmp}")
    print(f"fixed other garbage sources         : {fixed_other_garbage}")
    print(f"fixed garbled titles                 : {fixed_garbled}")
    print(f"filled missing dates                : {filled_date}")
    print(f"filled missing licenses             : {filled_license}")
    print(f"unresolved (Commons 404 / no meta)  : {unresolved} -> set to generic 'Wikimedia Commons'")

    if _dry:
        print("[dry-run] not writing index.html")
        return

    new_arr = json.dumps(data, ensure_ascii=False).replace("</script", "<\\/script")
    # Cut at mi (BEFORE the marker) so we don't duplicate "const ALL_IMAGES = ".
    new_html = raw[:mi] + "const ALL_IMAGES = " + new_arr + raw[arr_end:]

    # sanity: exactly one marker, valid JSON, feedback intact
    assert new_html.count("const ALL_IMAGES = ") == 1, "marker duplicated!"
    json.loads(new_html[mi + len("const ALL_IMAGES = "):
                        new_html.rfind("]", mi, new_html.find(";\n\nlet feedback", mi)) + 1])
    assert "let feedback = {}" in new_html, "feedback declaration lost!"

    bak = HTML + ".bak." + time.strftime("%Y%m%d-%H%M%S")
    open(bak, "w", encoding="utf-8").write(raw)
    open(HTML, "w", encoding="utf-8").write(new_html)
    print(f"wrote index.html (backup: {os.path.basename(bak)})")


if __name__ == "__main__":
    main()
