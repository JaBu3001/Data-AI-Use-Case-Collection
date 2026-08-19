#!/usr/bin/env python3
"""Lokale Webseite fuer die gescrapte Casebase-Use-Case-Sammlung."""
import os
import sqlite3
from collections import OrderedDict

from flask import Flask, abort, g, render_template, request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "data", "casebase.db")

TAG_LABELS = OrderedDict([
    ("family", "Use Case Cluster"),
    ("domain", "Geschäftsbereich"),
    ("process", "Prozess"),
    ("industry", "Branche"),
    ("target", "Zielgruppe"),
    ("risk", "Risikoklasse (EU AI Act)"),
])

app = Flask(__name__)


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def tags_for(post_ids):
    """{post_id: [(type, name), ...]} fuer eine Menge von Use Cases."""
    if not post_ids:
        return {}
    marks = ",".join("?" * len(post_ids))
    rows = db().execute(
        f"""SELECT uct.post_id, t.type, t.name FROM use_case_tags uct
            JOIN tags t ON t.id = uct.tag_id
            WHERE uct.post_id IN ({marks})
            ORDER BY t.type, t.name""", list(post_ids)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["post_id"], []).append((r["type"], r["name"]))
    return out


@app.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    active = {t: request.args.getlist(t) for t in TAG_LABELS}

    sql = ["SELECT uc.* FROM use_cases uc WHERE 1=1"]
    params = []
    if q:
        sql.append("""AND (uc.title LIKE ? OR uc.challenge LIKE ?
                           OR uc.solution LIKE ? OR uc.benefits LIKE ?)""")
        params += [f"%{q}%"] * 4
    for ttype, values in active.items():
        for val in values:  # UND zwischen Filtergruppen und innerhalb (kumulativ)
            sql.append("""AND EXISTS (SELECT 1 FROM use_case_tags x JOIN tags t ON t.id = x.tag_id
                           WHERE x.post_id = uc.post_id AND t.type = ? AND t.name = ?)""")
            params += [ttype, val]
    sql.append("ORDER BY uc.title COLLATE NOCASE")
    rows = db().execute(" ".join(sql), params).fetchall()

    by_id = tags_for([r["post_id"] for r in rows])
    cases = [{"row": r, "tags": by_id.get(r["post_id"], [])} for r in rows]

    facets = OrderedDict()
    for ttype, label in TAG_LABELS.items():
        facets[ttype] = {
            "label": label,
            "items": db().execute(
                """SELECT t.name, COUNT(*) AS n FROM tags t
                   JOIN use_case_tags x ON x.tag_id = t.id
                   WHERE t.type = ? GROUP BY t.name ORDER BY n DESC, t.name""",
                (ttype,)).fetchall(),
        }

    total = db().execute("SELECT COUNT(*) FROM use_cases").fetchone()[0]
    return render_template("index.html", cases=cases, facets=facets, active=active,
                           q=q, total=total, tag_labels=TAG_LABELS)


@app.route("/use-case/<int:post_id>")
def detail(post_id):
    row = db().execute("SELECT * FROM use_cases WHERE post_id = ?", (post_id,)).fetchone()
    if row is None:
        abort(404)
    grouped = OrderedDict((t, []) for t in TAG_LABELS)
    for ttype, name in tags_for([post_id]).get(post_id, []):
        grouped.setdefault(ttype, []).append(name)

    related = db().execute(
        """SELECT uc.post_id, uc.title, uc.image_file, uc.image_url, COUNT(*) AS shared
           FROM use_case_tags a
           JOIN use_case_tags b ON b.tag_id = a.tag_id AND b.post_id != a.post_id
           JOIN use_cases uc ON uc.post_id = b.post_id
           JOIN tags t ON t.id = a.tag_id
           WHERE a.post_id = ? AND t.type IN ('family','domain','process')
           GROUP BY uc.post_id ORDER BY shared DESC, uc.title LIMIT 4""", (post_id,)).fetchall()

    return render_template("detail.html", uc=row, grouped=grouped,
                           tag_labels=TAG_LABELS, related=related)


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        raise SystemExit("data/casebase.db fehlt – erst 'python scrape.py' ausführen.")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    app.run(host=args.host, port=args.port, debug=True)
