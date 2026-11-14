#!/usr/bin/env python3
"""AI daily watch (fallback multi-fournisseurs) for new FPV motors and missing specs.

Replaces / complements manual Claude daily sessions when Claude credits run out.
Tries configured AI providers in order and automatically falls back to the next
one on quota/credit exhaustion (402/429) or transient errors:
  1. Gemini API      (GEMINI_API_KEY, supports Google Search grounding)
  2. OpenRouter API  (OPENROUTER_API_KEY)
  3. OpenAI API      (OPENAI_API_KEY)
  4. Anthropic API   (ANTHROPIC_API_KEY)

If no API key is configured, exits 0 cleanly without failing the workflow.

Usage: python tools/ai_veille.py [--enrich-limit 15] [--dry-run]
"""
import argparse, csv, datetime as dt, html, json, os, re, sys, time
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from enrich import plausible  # noqa: E402
from shop_motors import ALIASES, key  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CAT = ROOT / "catalogue" / "moteurs.csv"
ENRICH_LOG = ROOT / "catalogue" / "enrichissement.csv"
ADDED_LOG = ROOT / "catalogue" / "ajouts_boutiques.csv"
EXCLUDED = ROOT / "catalogue" / "exclus.txt"
VEILLE_DIR = ROOT / "catalogue" / "veille"

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
EXTRACT_COLS = [
    "POIDS", "H MOTEUR", "D MOTEUR", "D SHAFT", "L SHAFT", "TYPE SHAFT",
    "VIS HEL", "VIS FIX", "ENTRAXE FIX", "LIPO", "VOLTAGE", "L CABLE",
    "TYPE CABLE", "HELICE", "PUISSANCE", "AMP", "AIMANT", "CLOCHE",
    "CONFIG", "RESISTANCE", "UTILISATION",
]
UNNAMED = re.compile(r"KV · [\d.]+ g$")


def _strip_html(raw: str, limit: int = 12000) -> str:
    txt = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(re.sub(r"\s+", " ", txt)).strip()
    return txt[:limit]


def _fetch_page(url: str) -> str:
    try:
        r = requests.get(url, headers=UA, timeout=25)
        if r.ok and "text/html" in r.headers.get("Content-Type", "text/html"):
            return _strip_html(r.text)
    except requests.RequestException:
        pass
    return ""


def _parse_json_block(text: str):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# --- Multi-provider AI client with automatic fallback ------------------------

def _call_gemini(prompt: str, use_search: bool = False, env_var: str = "GEMINI_API_KEY", label: str = "Gemini") -> tuple[str, str]:
    api_key = os.environ.get(env_var, "").strip()
    if not api_key:
        raise RuntimeError(f"{env_var} absente")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1},
    }
    if use_search:
        body["tools"] = [{"google_search": {}}]
    else:
        body["generationConfig"]["responseMimeType"] = "application/json"
    r = requests.post(url, json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"{label} HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    parts = data["candidates"][0]["content"]["parts"]
    text = "\n".join(p.get("text", "") for p in parts if "text" in p)
    return text, f"{label} ({model})"


def _call_openai_compat(url: str, api_key: str, model: str, label: str, prompt: str) -> tuple[str, str]:
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "temperature": 0.1,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"{label} HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    return data["choices"][0]["message"]["content"], f"{label} ({model})"


def _call_openrouter(prompt: str, use_search: bool = False) -> tuple[str, str]:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY absente")
    model = os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash")
    return _call_openai_compat("https://openrouter.ai/api/v1/chat/completions", api_key, model, "OpenRouter", prompt)


def _call_openai(prompt: str, use_search: bool = False) -> tuple[str, str]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY absente")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    return _call_openai_compat("https://api.openai.com/v1/chat/completions", api_key, model, "OpenAI", prompt)


def _call_anthropic(prompt: str, use_search: bool = False, env_var: str = "ANTHROPIC_API_KEY", label: str = "Anthropic") -> tuple[str, str]:
    api_key = os.environ.get(env_var, "").strip()
    if not api_key:
        raise RuntimeError(f"{env_var} absente")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": model, "max_tokens": 2048, "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"{label} HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    return data["content"][0]["text"], f"{label} ({model})"


PROVIDERS = [
    lambda p, use_search=False: _call_gemini(p, use_search=use_search, env_var="GEMINI_KEY_DOUZILS", label="Gemini Douzils"),
    lambda p, use_search=False: _call_gemini(p, use_search=use_search, env_var="GEMINI_KEY_TOM", label="Gemini Tom"),
    lambda p, use_search=False: _call_gemini(p, use_search=use_search, env_var="GEMINI_API_KEY", label="Gemini"),
    lambda p, use_search=False: _call_anthropic(p, use_search=use_search, env_var="CLAUDE_KEY_DOUZILS", label="Claude Douzils"),
    lambda p, use_search=False: _call_anthropic(p, use_search=use_search, env_var="ANTHROPIC_API_KEY", label="Anthropic"),
    _call_openrouter,
    _call_openai,
]

KEY_VARS = (
    "GEMINI_KEY_DOUZILS",
    "GEMINI_KEY_TOM",
    "GEMINI_API_KEY",
    "CLAUDE_KEY_DOUZILS",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
)


def ask_ai(prompt: str, use_search: bool = False) -> tuple[str, str]:
    errors = []
    for fn in PROVIDERS:
        try:
            return fn(prompt, use_search=use_search)
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(" | ".join(errors))


def has_any_key() -> bool:
    return any(os.environ.get(k, "").strip() for k in KEY_VARS)


# --- Catalogue helpers -------------------------------------------------------

def _load_excluded() -> set[str]:
    if not EXCLUDED.exists():
        return set()
    return {line.strip().lower() for line in EXCLUDED.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}


def _make_ref(brand: str, cls: str, kv: str, existing_refs: set[str]) -> str:
    prefix = re.sub(r"[^A-Z0-9]", "", brand.upper())[:4] or "MOTO"
    cls_clean = re.sub(r"[^0-9.]", "", cls) or "0000"
    kv_clean = re.sub(r"[^0-9]", "", str(kv)) or "0"
    base = f"{prefix}-{cls_clean}-{kv_clean}"
    ref = base
    n = 2
    while ref in existing_refs:
        ref = f"{base}-{n}"
        n += 1
    existing_refs.add(ref)
    return ref


def _append_csv_rows(path: Path, fieldnames: list[str], rows: list[dict]):
    if not rows:
        return
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            w.writeheader()
        for row in rows:
            w.writerow(row)


# --- Tasks -------------------------------------------------------------------

def discover_new_motors(rows: list[dict], cols: list[str], dry_run: bool) -> tuple[list[dict], list[str], str]:
    """Use AI (with web search when supported) to find recently released FPV motors."""
    known_keys = {key(f"{r['MARQUE']} {r['NOM']} {r['KV']}") for r in rows}
    existing_refs = {r["REF"] for r in rows if r.get("REF")}
    excluded = _load_excluded()
    next_id = max((int(r["ID"]) for r in rows if str(r.get("ID", "")).isdigit()), default=0) + 1

    today = dt.date.today().isoformat()
    prompt = f"""Date du jour : {today}.
Cherche sur le web les moteurs brushless pour drones FPV sortis récemment (2025-2026) chez les fabricants reconnus
(T-Motor, EMAX, iFlight, BetaFPV, FlyFishRC, RCinPower, Axisflying, BrotherHobby, Xing, Hobbywing, Foxeer, GEPRC, Happymodel, Sub250, SpeedyBee, FlashHobby).

Retourne UNIQUEMENT un tableau JSON (max 10 moteurs, 1 entrée par KV) avec des données vérifiées sur une fiche officielle ou boutique.
Si tu n'as pas de recherche web ou aucun moteur vérifié, retourne [].

Format exact de chaque objet :
{{
  "MARQUE": "T-Motor",
  "NOM": "Velox V3 2207",
  "CLASSE": "2207",
  "KV": "1950",
  "POIDS": "34.5",
  "H STATOR": "7",
  "D STATOR": "22",
  "H MOTEUR": "31.2",
  "D MOTEUR": "27.5",
  "D SHAFT": "5",
  "LIPO": "6S",
  "PUISSANCE": "950",
  "AMP": "40",
  "RESISTANCE": "52",
  "CONFIG": "12N14P",
  "LIEN": "https://..."
}}
Ne mets que les champs dont tu es sûr (laisse "" sinon). Retourne uniquement du JSON valide."""

    text, provider = ask_ai(prompt, use_search=True)
    items = _parse_json_block(text)
    if not isinstance(items, list):
        return [], [f"Réponse découverte non JSON ({provider})"], provider

    added_rows, added_log, notes = [], [], []
    for item in items:
        if not isinstance(item, dict):
            continue
        brand = str(item.get("MARQUE", "")).strip()
        brand = ALIASES.get(brand.lower(), brand)
        nom = str(item.get("NOM", "")).strip()
        kv = re.sub(r"[^\d]", "", str(item.get("KV", "")))
        cls = re.sub(r"[^\d.]", "", str(item.get("CLASSE", "")))
        link = str(item.get("LIEN", "")).strip()
        if not (brand and nom and kv and cls and link.startswith("http")):
            continue
        if any(ex in f"{brand} {nom}".lower() for ex in excluded):
            continue
        k = key(f"{brand} {nom} {kv}")
        if k in known_keys:
            continue

        new_row = {c: "" for c in cols}
        new_row["ID"] = str(next_id)
        next_id += 1
        new_row["REF"] = _make_ref(brand, cls, kv, existing_refs)
        new_row["MARQUE"] = brand
        new_row["NOM"] = nom
        new_row["CLASSE"] = cls
        new_row["KV"] = kv
        new_row["LIEN"] = link

        # Derive stator if missing
        m_cls = re.match(r"^(\d{2})(\d{2}(?:\.\d+)?)$", cls)
        if m_cls:
            new_row["D STATOR"] = str(item.get("D STATOR") or int(m_cls.group(1)))
            new_row["H STATOR"] = str(item.get("H STATOR") or m_cls.group(2))

        for c in ("POIDS", "H MOTEUR", "D MOTEUR", "D SHAFT", "LIPO", "PUISSANCE", "AMP", "RESISTANCE", "CONFIG"):
            val = str(item.get(c, "")).strip()
            if val and plausible(new_row, c, val):
                new_row[c] = val

        known_keys.add(k)
        added_rows.append(new_row)
        added_log.append({
            "REF": new_row["REF"],
            "MARQUE": brand,
            "NOM": nom,
            "KV": kv,
            "SOURCE": link,
            "TITRE": f"AI Veille ({provider})",
        })
        notes.append(f"- `{new_row['REF']}` — {brand} {nom} {kv}KV ([source]({link}))")

    if not dry_run and added_rows:
        rows.extend(added_rows)
        _append_csv_rows(ADDED_LOG, ["REF", "MARQUE", "NOM", "KV", "SOURCE", "TITRE"], added_log)

    return added_rows, notes, provider


def enrich_incomplete_motors(rows: list[dict], limit: int, dry_run: bool) -> tuple[int, list[str], str]:
    """Fetch motor URLs for incomplete rows and extract missing fields via AI."""
    candidates = []
    for r in rows:
        if UNNAMED.search(r.get("NOM", "")) or not str(r.get("LIEN", "")).startswith("http"):
            continue
        missing = [c for c in EXTRACT_COLS if not (r.get(c) or "").strip()]
        if len(missing) >= 6:
            candidates.append((len(missing), r, missing))
    candidates.sort(key=lambda x: -x[0])

    filled_count = 0
    enrich_logs, notes = [], []
    provider_used = ""

    for _, row, missing in candidates[:limit]:
        page_text = _fetch_page(row["LIEN"])
        if len(page_text) < 200:
            continue
        prompt = f"""Extrais uniquement les caractéristiques techniques manquantes pour ce moteur FPV :
Moteur : {row['MARQUE']} {row['NOM']} {row['KV']}KV (classe {row['CLASSE']})
Champs manquants autorisés : {json.dumps(missing)}

Règles :
- Retourne UNIQUEMENT un objet JSON avec les clés présentes dans la liste ci-dessus qui sont explicitement dans le texte.
- Ne devine rien. Si une valeur est absente ou ambiguë, omets la clé.
- Unités : POIDS en g (nombre seul ex: "32.5"), H MOTEUR / D MOTEUR / D SHAFT / L SHAFT en mm (nombre seul), PUISSANCE en W (nombre seul), AMP en A (nombre seul), RESISTANCE en mΩ (nombre seul).

Texte de la page ({row['LIEN']}) :
{page_text}"""

        try:
            text, provider = ask_ai(prompt, use_search=False)
            provider_used = provider
        except Exception as exc:
            notes.append(f"- Erreur IA sur `{row['REF']}` : {exc}")
            break

        data = _parse_json_block(text)
        if not isinstance(data, dict):
            continue

        added_here = []
        for col in missing:
            val = str(data.get(col, "")).strip()
            if not val or not plausible(row, col, val):
                continue
            if not dry_run:
                row[col] = val
                enrich_logs.append({"REF": row["REF"], "CHAMP": col, "VALEUR": val, "SOURCE": row["LIEN"]})
            filled_count += 1
            added_here.append(f"{col}={val}")

        if added_here:
            notes.append(f"- `{row['REF']}` ({row['MARQUE']} {row['NOM']}) : {', '.join(added_here)}")
        time.sleep(0.5)

    if not dry_run and enrich_logs:
        _append_csv_rows(ENRICH_LOG, ["REF", "CHAMP", "VALEUR", "SOURCE"], enrich_logs)

    return filled_count, notes, provider_used


def main():
    ap = argparse.ArgumentParser(description="AI daily watch with multi-provider fallback")
    ap.add_argument("--enrich-limit", type=int, default=15, help="Max incomplete motors to inspect")
    ap.add_argument("--dry-run", action="store_true", help="Do not modify files")
    args = ap.parse_args()

    if not has_any_key():
        print("ai_veille: aucune clé API configurée (GEMINI_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY) — étape ignorée.")
        return

    with CAT.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        cols = list(reader.fieldnames or [])
        rows = list(reader)

    today = dt.date.today().isoformat()
    added_rows, added_notes, disc_provider = [], [], ""
    try:
        added_rows, added_notes, disc_provider = discover_new_motors(rows, cols, args.dry_run)
    except Exception as exc:
        added_notes = [f"- Découverte web indisponible : {exc}"]

    filled_count, enrich_notes, enr_provider = enrich_incomplete_motors(rows, args.enrich_limit, args.dry_run)

    if not args.dry_run and (added_rows or filled_count > 0):
        with CAT.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=cols)
            writer.writeheader()
            writer.writerows(rows)

    if not args.dry_run:
        VEILLE_DIR.mkdir(parents=True, exist_ok=True)
        report = VEILLE_DIR / f"{today}.md"
        if not report.exists():
            providers = ", ".join(p for p in dict.fromkeys([disc_provider, enr_provider]) if p) or "aucun"
            lines = [
                f"# Veille IA du catalogue — {today}",
                "",
                f"Fournisseur(s) utilisé(s) : {providers}",
                f"Bilan : {len(added_rows)} nouveau(x) moteur(s) ajouté(s), {filled_count} champ(s) complété(s).",
                "",
                "## Nouveaux moteurs",
                *(added_notes or ["Aucun."]),
                "",
                "## Fiches complétées",
                *(enrich_notes or ["Aucune."]),
                "",
            ]
            report.write_text("\n".join(lines), encoding="utf-8")

    print(f"ai_veille: +{len(added_rows)} moteurs, +{filled_count} champs ({disc_provider or enr_provider or 'aucun'})")


if __name__ == "__main__":
    main()
