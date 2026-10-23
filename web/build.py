"""Embed data/suppliers.json into the page: web/template.html -> dist/index.html."""

import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
data = json.loads((root / "data/suppliers.json").read_text())
page = (root / "web/template.html").read_text()
blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
# Optional demo video: VIDEO_URL (an artifact asset url) and a poster image.
import base64, os
video = os.environ.get("VIDEO_URL", "")
poster = os.environ.get("VIDEO_POSTER")
if video:
    page = page.replace("/*__VIDEO__*/", video, 1)
if poster:
    page = page.replace("/*__POSTER__*/", "data:image/jpeg;base64," + base64.b64encode(Path(poster).read_bytes()).decode(), 1)
out = root / "dist/index.html"
out.parent.mkdir(exist_ok=True)
out.write_text(page.replace("/*__DATA__*/null", blob, 1))
print(f"{out.relative_to(root)}: {len(data['suppliers'])} suppliers, {out.stat().st_size // 1024} KB")
