import json

import pandas as pd

from orbitcu import sitemap


def test_build_and_render_from_minimal_results(tmp_path):
    ai = tmp_path / "activity_index"
    ai.mkdir()
    pd.DataFrame({"site_id": ["PL-GLOGOW", "PL-GLOGOW"], "week_end": ["2026-10-18", "2026-10-25"],
                  "activity": [0.8, 0.1], "clear_days": [20, 20], "ref": [0.8, 0.8], "r": [1.0, 0.12],
                  "visible": [True, True], "status_raw": ["running", "stopped"],
                  "status": ["running", "stopped"]}).to_csv(ai / "weekly_status.csv", index=False)
    data = sitemap.build_data(tmp_path)
    assert data["weeks"] == ["2026-10-18", "2026-10-25"]
    glogow = next(s for s in data["sites"] if s["id"] == "PL-GLOGOW")
    assert glogow["status"] == "rs" and glogow["r"] == [1.0, 0.12]
    moved = [s["id"] for s in data["sites"] if s["moved"]]
    assert "CD-KAMOA" in moved
    out = sitemap.render(data, tmp_path / "map.html")
    html = out.read_text(encoding="utf-8")
    assert "/*__DATA__*/" not in html and "cdn.jsdelivr.net" not in html and "fonts.googleapis.com" not in html
    start = html.index("const DATA = ") + len("const DATA = ")
    json.loads(html[start:html.index(";\nconst WORLD")])
