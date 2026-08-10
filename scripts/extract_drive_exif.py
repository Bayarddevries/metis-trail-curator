#!/usr/bin/env python3
"""
Extract embedded provenance (XPSubject / ImageDescription / XPTitle EXIF tags)
from every image in the shared Google Drive folder, cache to drive_exif_cache.json.

The Windows "Subject" field the user sees is the file's XPSubject EXIF tag.
exiftool reads it. We download each image once and extract the tags locally
(avoids re-hitting the Drive API on every parse iteration).
"""
import json, os, subprocess, urllib.request, urllib.parse, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "drive_folder_cache.json")
OUT = os.path.join(ROOT, "drive_exif_cache.json")
DL = os.path.join(ROOT, "drive_downloads")
os.makedirs(DL, exist_ok=True)

tok_path = os.path.expanduser("~/.hermes/google_token.json")
secret = json.load(open(os.path.expanduser("~/.hermes/google_client_secret.json")))["installed"]
token = json.load(open(tok_path))

def refresh():
    body = {"client_id": secret["client_id"], "client_secret": secret["client_secret"],
            "refresh_token": token["refresh_token"], "grant_type": "refresh_token"}
    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    r = json.load(urllib.request.urlopen(req))
    token["access_token"] = r["access_token"]
    json.dump(token, open(tok_path, "w"))
    return r["access_token"]

at = token.get("access_token") or refresh()

def extract(path):
    out = subprocess.run(["exiftool", "-XPSubject", "-ImageDescription", "-XPTitle",
                          "-j", path], capture_output=True, text=True).stdout
    try:
        j = json.loads(out)[0]
    except Exception:
        return {}
    return {
        "xpsubject": (j.get("XPSubject") or "").strip(),
        "imagedesc": (j.get("ImageDescription") or "").strip(),
        "xptitle": (j.get("XPTitle") or "").strip(),
    }

files = json.load(open(CACHE))
results = []
for f in files:
    if not f.get("mimeType", "").startswith("image/"):
        continue
    fn = f["name"]
    path = os.path.join(DL, fn)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        url = f"https://www.googleapis.com/drive/v3/files/{f['id']}?alt=media"
        try:
            data = urllib.request.urlopen(
                urllib.request.Request(url, headers={"Authorization": f"Bearer {at}"}), timeout=60).read()
            open(path, "wb").write(data)
        except Exception as e:
            print(f"download failed {fn}: {e}", file=sys.stderr)
            continue
    ex = extract(path)
    results.append({
        "name": fn,
        "id": f["id"],
        "drive_description": f.get("description", "") or "",
        "xpsubject": ex.get("xpsubject", ""),
        "imagedesc": ex.get("imagedesc", ""),
        "xptitle": ex.get("xptitle", ""),
    })

json.dump(results, open(OUT, "w"), indent=2, ensure_ascii=False)
n_xp = sum(1 for r in results if r["xpsubject"])
print(f"extracted {len(results)} images; {n_xp} with XPSubject")
