#!/usr/bin/env python3
"""
Comprehensive EXIF/XMP metadata extractor for the shared Drive folder images.

The Windows "Details" tab exposes several fields, each backed by a different
tag family:
  - Subject  -> IFD0:XPSubject (+ XMP-dc:Description)
  - Copyright/Rights -> IFD0:Copyright (+ XMP-dc:Rights)
  - Title    -> IFD0:XPTitle (+ XMP-dc:Title)
  - Comments -> IFD0:XPComment
  - Tags/Keywords -> IFD0:XPKeywords (+ XMP-dc:Subject)
  - Authors  -> IFD0:Artist / XMP-dc:Creator
  - Date taken -> EXIF:DateTimeOriginal / XMP-xmp:CreateDate
  - Credit / Source -> Photoshop:Credit / Source
  - Usage terms -> XMP-xmpRights:UsageTerms

We pull ALL of these (plus a JSON dump) so nothing is lost, and store a
structured record per file. The merge step decides what maps where.
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "drive_folder_cache.json")
OUT = os.path.join(ROOT, "drive_full_metadata_cache.json")
DL = os.path.join(ROOT, "drive_downloads")
os.makedirs(DL, exist_ok=True)

# tag groups exiftool should read (the human-relevant ones)
TAGS = ["XPSubject", "Copyright", "XPTitle", "XPComment", "XPKeywords",
        "Artist", "ImageDescription", "DateTimeOriginal",
        "Photoshop:Credit", "Photoshop:Source", "Photoshop:AuthorsPosition",
        "XMP-dc:Title", "XMP-dc:Rights", "XMP-dc:Description", "XMP-dc:Creator",
        "XMP-dc:Subject", "XMP-xmp:CreateDate", "XMP-xmp:Label",
        "XMP-xmpRights:UsageTerms", "XMP-xmpRights:Marked",
        "XMP-photoshop:City", "XMP-photoshop:State", "XMP-photoshop:Country",
        "XMP-tiff:Copyright", "Make", "Model"]

def extract(path):
    cmd = ["exiftool", "-a", "-G1", "-s", "-j"] + [f"-{t}" for t in TAGS] + [path]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    try:
        j = json.loads(out)[0]
    except Exception:
        return {}
    # flatten: "Group:Tag" -> value
    return {k: v for k, v in j.items() if v not in (None, "", [])}

files = json.load(open(CACHE))
results = []
for f in files:
    if not f.get("mimeType", "").startswith("image/"):
        continue
    fn = f["name"]
    path = os.path.join(DL, fn)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        continue
    rec = extract(path)
    rec["_name"] = fn
    rec["_drive_description"] = f.get("description", "") or ""
    results.append(rec)

json.dump(results, open(OUT, "w"), indent=2, ensure_ascii=False)
# summary: how many have each field
from collections import Counter
cnt = Counter()
for r in results:
    for k in r:
        if k.startswith("_"):
            continue
        cnt[k] += 1
print(f"extracted {len(results)} images")
print("field coverage:")
for k, c in cnt.most_common():
    print(f"  {k}: {c}")
