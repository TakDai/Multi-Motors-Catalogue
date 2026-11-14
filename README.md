# Multi-Motors — Catalogue Public & API Communauté

Base de données ouverte et mise à jour quotidiennement des moteurs brushless pour drones FPV (utilisée par [multi-motors.fr](https://multi-motors.fr)).

---

## Télécharger le CSV

- **Téléchargement direct (`moteurs.csv`)** :
  https://raw.githubusercontent.com/TakDai/Multi-Motors-Catalogue/main/catalogue/moteurs.csv

---

## API JSON gratuite (sans clé, mise à jour quotidienne)

Les fichiers JSON statiques sont générés automatiquement à chaque mise à jour dans `api/` :

| Endpoint | Contenu | URL directe |
|---|---|---|
| `moteurs.json` | Tous les moteurs + `updated_at` + `count` | https://raw.githubusercontent.com/TakDai/Multi-Motors-Catalogue/main/api/moteurs.json |
| `marques.json` | Liste des marques et nombre de moteurs | https://raw.githubusercontent.com/TakDai/Multi-Motors-Catalogue/main/api/marques.json |
| `stats.json` | Statistiques globales du catalogue | https://raw.githubusercontent.com/TakDai/Multi-Motors-Catalogue/main/api/stats.json |
| `moteurs.csv` | Copie CSV dans `api/` | https://raw.githubusercontent.com/TakDai/Multi-Motors-Catalogue/main/api/moteurs.csv |

### Exemples d'utilisation

**JavaScript / Web :**
```js
const res = await fetch("https://raw.githubusercontent.com/TakDai/Multi-Motors-Catalogue/main/api/moteurs.json");
const { count, updated_at, motors } = await res.json();
```

**Python :**
```python
import requests
data = requests.get("https://raw.githubusercontent.com/TakDai/Multi-Motors-Catalogue/main/api/moteurs.json").json()
print(data["count"], data["motors"][0])
```

**cURL :**
```bash
curl -L -o moteurs.csv https://raw.githubusercontent.com/TakDai/Multi-Motors-Catalogue/main/catalogue/moteurs.csv
```

---

## Structure du dépôt

- `catalogue/` — Source de vérité (`moteurs.csv`, journaux d'enrichissement, rapports de veille IA)
- `api/` — Exports publics prêts à consommer (`moteurs.csv`, `moteurs.json`, `marques.json`, `stats.json`)
- `data/` — Données techniques associées (prix, photos, vidéos, fiches fabricants, classements)
- `assets/` — Miniatures et logos des moteurs
- `tools/` — Scripts Python d'import, scraping, enrichissement, veille IA et génération d'API
