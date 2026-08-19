#!/usr/bin/env python3
"""Lokale Webseite fuer die gescrapte Casebase-Use-Case-Sammlung."""
import os
import re
import sqlite3
import unicodedata
from collections import OrderedDict
from datetime import datetime, timezone

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   url_for)
from werkzeug.utils import secure_filename

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "data", "casebase.db")
IMG_DIR = os.path.join(APP_DIR, "static", "img")
ALLOWED_IMG = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
LOCAL_ID_BASE = 900000  # eigene Use Cases bekommen IDs oberhalb der gescrapten

TAG_LABELS = OrderedDict([
    ("family", "Use Case Cluster"),
    ("domain", "Geschäftsbereich"),
    ("process", "Prozess"),
    ("industry", "Branche"),
    ("target", "Zielgruppe"),
    ("risk", "Risikoklasse (EU AI Act)"),
])

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "casebase-local-dev")


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


def slugify(text):
    text = (text or "").lower()
    for src, dst in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(src, dst)
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "use-case"


def to_html(text):
    """Textarea-Eingabe zu einfachem HTML – vorhandenes Markup bleibt unangetastet."""
    text = (text or "").strip()
    if not text:
        return ""
    if re.search(r"<(p|ul|ol|li|br|h[1-6]|div|table)\b", text, re.I):
        return text
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    return "".join("<p>%s</p>" % b.replace("\n", "<br>") for b in blocks)


def from_html(html):
    """Grober Rueckweg HTML -> Textarea, damit Bearbeiten nicht nach Code aussieht."""
    text = html or ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h[1-6])>", "\n\n", text)
    text = re.sub(r"(?i)</li>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_tag_input(value):
    """'A, B; C' -> ['A', 'B', 'C'] (ohne Duplikate, Reihenfolge bleibt)."""
    parts = [p.strip() for p in re.split(r"[,;\n]", value or "")]
    out = []
    for p in parts:
        if p and p not in out:
            out.append(p)
    return out


def set_tags(con, post_id, tagmap):
    con.execute("DELETE FROM use_case_tags WHERE post_id = ?", (post_id,))
    for ttype, names in tagmap.items():
        for name in names:
            con.execute("INSERT OR IGNORE INTO tags (type, name) VALUES (?, ?)", (ttype, name))
            tag_id = con.execute("SELECT id FROM tags WHERE type = ? AND name = ?",
                                 (ttype, name)).fetchone()[0]
            con.execute("INSERT OR IGNORE INTO use_case_tags (post_id, tag_id) VALUES (?, ?)",
                        (post_id, tag_id))
    con.execute("""DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM use_case_tags)""")


def all_tag_names():
    """{type: [name, ...]} als Vorschlagsliste fuer das Formular."""
    out = OrderedDict((t, []) for t in TAG_LABELS)
    for r in db().execute("SELECT type, name FROM tags ORDER BY type, name COLLATE NOCASE"):
        out.setdefault(r["type"], []).append(r["name"])
    return out


def save_image(file_storage, post_id):
    """Optionaler Upload -> static/img/<post_id><ext>; gibt Dateinamen zurueck."""
    if not file_storage or not file_storage.filename:
        return None
    ext = os.path.splitext(secure_filename(file_storage.filename))[1].lower()
    if ext not in ALLOWED_IMG:
        raise ValueError("Bildformat nicht erlaubt (%s)" % ", ".join(sorted(ALLOWED_IMG)))
    os.makedirs(IMG_DIR, exist_ok=True)
    name = "%s%s" % (post_id, ext)
    file_storage.save(os.path.join(IMG_DIR, name))
    return name


def form_payload(req):
    """Formularwerte einlesen (fuer Speichern und fuer Re-Render bei Fehlern)."""
    return {
        "title": (req.form.get("title") or "").strip(),
        "challenge": (req.form.get("challenge") or "").strip(),
        "solution": (req.form.get("solution") or "").strip(),
        "benefits": (req.form.get("benefits") or "").strip(),
        "image_url": (req.form.get("image_url") or "").strip(),
        "source_name": (req.form.get("source_name") or "").strip(),
        "source_url": (req.form.get("source_url") or "").strip(),
        "url": (req.form.get("url") or "").strip(),
        "risk_class": (req.form.get("risk_class") or "").strip(),
        "risk_reference": (req.form.get("risk_reference") or "").strip(),
        "risk_obligation": (req.form.get("risk_obligation") or "").strip(),
        "tags": {t: parse_tag_input(req.form.get("tag_" + t)) for t in TAG_LABELS},
    }


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


@app.route("/use-case/new", methods=["GET", "POST"])
def create():
    if request.method == "GET":
        return render_template("form.html", data=None, tags={t: [] for t in TAG_LABELS},
                               tag_labels=TAG_LABELS, suggestions=all_tag_names(), mode="new")

    data = form_payload(request)
    if not data["title"]:
        flash("Titel ist ein Pflichtfeld.", "error")
        return render_template("form.html", data=data, tags=data["tags"], tag_labels=TAG_LABELS,
                               suggestions=all_tag_names(), mode="new"), 400

    con = db()
    max_id = con.execute("SELECT MAX(post_id) FROM use_cases").fetchone()[0] or 0
    post_id = max(max_id + 1, LOCAL_ID_BASE)
    try:
        image_file = save_image(request.files.get("image"), post_id)
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template("form.html", data=data, tags=data["tags"], tag_labels=TAG_LABELS,
                               suggestions=all_tag_names(), mode="new"), 400

    con.execute(
        """INSERT INTO use_cases (post_id, lang, slug, title, url, image_url, image_file,
                                  challenge, solution, benefits, source_name, source_url,
                                  risk_class, risk_reference, risk_obligation, scraped_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (post_id, "de", slugify(data["title"]), data["title"], data["url"],
         data["image_url"], image_file, to_html(data["challenge"]), to_html(data["solution"]),
         to_html(data["benefits"]), data["source_name"], data["source_url"],
         data["risk_class"], data["risk_reference"], data["risk_obligation"],
         datetime.now(timezone.utc).isoformat(timespec="seconds")))
    set_tags(con, post_id, data["tags"])
    con.commit()
    flash("Use Case angelegt.", "ok")
    return redirect(url_for("detail", post_id=post_id))


@app.route("/use-case/<int:post_id>/edit", methods=["GET", "POST"])
def edit(post_id):
    row = db().execute("SELECT * FROM use_cases WHERE post_id = ?", (post_id,)).fetchone()
    if row is None:
        abort(404)

    if request.method == "GET":
        grouped = OrderedDict((t, []) for t in TAG_LABELS)
        for ttype, name in tags_for([post_id]).get(post_id, []):
            grouped.setdefault(ttype, []).append(name)
        data = dict(row)
        for field in ("challenge", "solution", "benefits"):
            data[field] = from_html(row[field])
        return render_template("form.html", data=data, tags=grouped, tag_labels=TAG_LABELS,
                               suggestions=all_tag_names(), mode="edit", post_id=post_id)

    data = form_payload(request)
    if not data["title"]:
        flash("Titel ist ein Pflichtfeld.", "error")
        return render_template("form.html", data=data, tags=data["tags"], tag_labels=TAG_LABELS,
                               suggestions=all_tag_names(), mode="edit", post_id=post_id), 400

    con = db()
    try:
        image_file = save_image(request.files.get("image"), post_id) or row["image_file"]
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template("form.html", data=data, tags=data["tags"], tag_labels=TAG_LABELS,
                               suggestions=all_tag_names(), mode="edit", post_id=post_id), 400

    con.execute(
        """UPDATE use_cases SET title=?, slug=?, url=?, image_url=?, image_file=?, challenge=?,
                                solution=?, benefits=?, source_name=?, source_url=?,
                                risk_class=?, risk_reference=?, risk_obligation=?
           WHERE post_id=?""",
        (data["title"], slugify(data["title"]), data["url"], data["image_url"], image_file,
         to_html(data["challenge"]), to_html(data["solution"]), to_html(data["benefits"]),
         data["source_name"], data["source_url"], data["risk_class"], data["risk_reference"],
         data["risk_obligation"], post_id))
    set_tags(con, post_id, data["tags"])
    con.commit()
    flash("Änderungen gespeichert.", "ok")
    return redirect(url_for("detail", post_id=post_id))


@app.route("/use-case/<int:post_id>/delete", methods=["POST"])
def delete(post_id):
    con = db()
    row = con.execute("SELECT title, image_file FROM use_cases WHERE post_id = ?",
                      (post_id,)).fetchone()
    if row is None:
        abort(404)
    con.execute("DELETE FROM use_case_tags WHERE post_id = ?", (post_id,))
    con.execute("DELETE FROM use_cases WHERE post_id = ?", (post_id,))
    con.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM use_case_tags)")
    con.commit()
    if post_id >= LOCAL_ID_BASE and row["image_file"]:  # nur selbst hochgeladene Bilder loeschen
        path = os.path.join(IMG_DIR, row["image_file"])
        if os.path.exists(path):
            os.remove(path)
    flash("„%s“ wurde gelöscht." % row["title"], "ok")
    return redirect(url_for("index"))


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        raise SystemExit("data/casebase.db fehlt – erst 'python scrape.py' ausführen.")
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    app.run(host=args.host, port=args.port, debug=True)
