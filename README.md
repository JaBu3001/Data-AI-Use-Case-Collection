# KI Use Case Sammlung – lokale Kopie

Scrapt die [Casebase AI Use Case Collection](https://casebase.ai/de/ai-use-case-collection/)
in eine lokale SQLite-DB und zeigt sie in einer eigenen Webseite mit Filtern und Volltextsuche an.

> Nur für den persönlichen Gebrauch. Alle Inhalte und Rechte liegen bei Casebase.ai;
> jeder Datensatz behält die Quell-URL und ggf. die Originalquelle.

## Setup

```bash
python3 -m venv .venv && .venv/bin/python -m ensurepip && .venv/bin/python -m pip install -r requirements.txt
```

## Daten holen / aktualisieren

```bash
.venv/bin/python scrape.py
```

Optionen: `--lang en` (englische Version), `--no-images`, `--limit N`, `--delay 0.6`.
Der Lauf ist idempotent – bestehende Datensätze werden per `post_id` aktualisiert.

## Webseite starten

```bash
.venv/bin/python app.py            # oder: --port 9000
```

→ http://127.0.0.1:8080

## Datenmodell (`data/casebase.db`)

| Tabelle | Inhalt |
|---|---|
| `use_cases` | `post_id` (PK), `title`, `slug`, `url`, Bild, `challenge`, `solution`, `benefits` (HTML), `source_name`/`source_url`, `risk_class`/`risk_reference`/`risk_obligation`, `scraped_at` |
| `tags` | `type` (`family`, `domain`, `process`, `industry`, `target`, `risk`) + `name`, unique |
| `use_case_tags` | n:m-Verknüpfung |

Beispielabfrage:

```bash
sqlite3 data/casebase.db "SELECT t.name, COUNT(*) FROM tags t JOIN use_case_tags x ON x.tag_id=t.id WHERE t.type='domain' GROUP BY 1 ORDER BY 2 DESC;"
```

Bilder liegen lokal unter `static/img/<post_id>.jpg`, die Seite funktioniert damit offline.
