#!/usr/bin/env python3
"""Build downloadable CSV and static JSON API files for the community in api/.

Generated files:
  - api/moteurs.csv    (direct copy of catalogue/moteurs.csv)
  - api/moteurs.json   ({"updated_at", "count", "motors": [...]})
  - api/marques.json   ({"updated_at", "brands": [{"name", "count"}, ...]})
  - api/stats.json     ({"updated_at", "total_motors", "total_brands", "total_classes"})
  - api/index.html     (API & CSV documentation page)
"""
import csv, datetime as dt, json, shutil
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAT = ROOT / "catalogue" / "moteurs.csv"
API = ROOT / "api"


def main():
    API.mkdir(parents=True, exist_ok=True)
    with CAT.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    shutil.copy2(CAT, API / "moteurs.csv")

    (API / "moteurs.json").write_text(
        json.dumps({"updated_at": now, "count": len(rows), "motors": rows}, ensure_ascii=False),
        encoding="utf-8",
    )

    brands = Counter(r.get("MARQUE", "").strip() for r in rows if r.get("MARQUE", "").strip())
    classes = Counter(r.get("CLASSE", "").strip() for r in rows if r.get("CLASSE", "").strip())

    (API / "marques.json").write_text(
        json.dumps(
            {"updated_at": now, "brands": [{"name": k, "count": v} for k, v in brands.most_common()]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (API / "stats.json").write_text(
        json.dumps(
            {
                "updated_at": now,
                "total_motors": len(rows),
                "total_brands": len(brands),
                "total_classes": len(classes),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (API / "index.html").write_text(
        f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Multi-Motors — API & CSV Communauté</title>
  <style>
    body{{font-family:system-ui,-apple-system,sans-serif;max-width:820px;margin:40px auto;padding:0 20px;line-height:1.6;color:#1e293b}}
    code,pre{{background:#f1f5f9;padding:2px 6px;border-radius:6px;font-size:.92em}}
    pre{{padding:14px;overflow-x:auto}}
    a{{color:#2563eb}}
  </style>
</head>
<body>
  <h1>Multi-Motors — Données publiques FPV</h1>
  <p>Base de données communautaire de <strong>{len(rows)}</strong> moteurs brushless FPV ({len(brands)} marques), mise à jour quotidiennement ({now}).</p>
  <h2>Téléchargement direct & Endpoints</h2>
  <ul>
    <li><a href="./moteurs.csv"><code>moteurs.csv</code></a> — Catalogue complet au format CSV (UTF-8)</li>
    <li><a href="./moteurs.json"><code>moteurs.json</code></a> — Catalogue complet au format JSON</li>
    <li><a href="./marques.json"><code>marques.json</code></a> — Liste des marques et nombre de modèles</li>
    <li><a href="./stats.json"><code>stats.json</code></a> — Statistiques globales et date de mise à jour</li>
  </ul>
  <h2>Exemple d'appel (JavaScript)</h2>
  <pre>const res = await fetch("https://raw.githubusercontent.com/TakDai/Multi-Motors-Catalogue/main/api/moteurs.json");
const data = await res.json();
console.log(data.count, data.motors[0]);</pre>
</body>
</html>
""",
        encoding="utf-8",
    )
    print(f"build_api: {len(rows)} moteurs exportés dans api/")


if __name__ == "__main__":
    main()
