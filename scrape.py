#!/usr/bin/env python3
"""Scrapt die Casebase AI-Use-Case-Collection in eine lokale SQLite-DB.

Nur fuer den persoenlichen Gebrauch. Inhalte bleiben Eigentum von casebase.ai;
jeder Datensatz behaelt die Quell-URL.

Aufruf:  python scrape.py [--lang de|en] [--no-images] [--limit N]
"""
import argparse
import os
import re
import sqlite3
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = "https://casebase.ai"
COLLECTION = {
    "de": f"{BASE}/de/ai-use-case-collection/",
    "en": f"{BASE}/en/ai-use-case-collection/",
}
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "casebase.db")
IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

SCHEMA = """
CREATE TABLE IF NOT EXISTS use_cases (
    post_id       INTEGER PRIMARY KEY,
    lang          TEXT NOT NULL,
    slug          TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    image_url     TEXT,
    image_file    TEXT,
    challenge     TEXT,
    solution      TEXT,
    benefits      TEXT,          -- HTML
    source_name   TEXT,
    source_url    TEXT,
    risk_class    TEXT,
    risk_reference TEXT,
    risk_obligation TEXT,
    scraped_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,          -- family | domain | process | target | industry | risk
    name TEXT NOT NULL,
    UNIQUE (type, name)
);
CREATE TABLE IF NOT EXISTS use_case_tags (
    post_id INTEGER NOT NULL REFERENCES use_cases(post_id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (post_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_uct_tag ON use_case_tags(tag_id);
"""


def get(session, url):
    r = session.get(url, timeout=45)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def text_of(node):
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else None


def inner_html(node):
    return "".join(str(c) for c in node.contents).strip() if node else None


def box_tags(soup, box_id):
    box = soup.find(id=box_id)
    if not box:
        return []
    return [text_of(s) for s in box.select("span.box--tag") if text_of(s)]


def parse_list(soup):
    """Karten der Uebersichtsseite -> Basisdaten + Tags."""
    items = []
    for item in soup.select(".sf-result-item"):
        a = item.find("a", class_="use-case-popup")
        if not a:
            continue
        img = item.find("img")
        tags = []
        for span in item.select(".use-case-tags span.tag"):
            classes = span.get("class", [])
            ttype = next((c.split("--", 1)[1] for c in classes
                          if c.startswith("tag--") and not c.startswith("tag--risk-")), None)
            if ttype:
                tags.append((ttype, text_of(span)))
        items.append({
            "post_id": int(a.get("data-post-id")),
            "url": a["href"],
            "title": text_of(item.find("h4")),
            "image_url": img["src"] if img and img.get("src") else None,
            "tags": tags,
        })
    return items


def parse_detail(soup):
    """Detailseite -> Langtexte, Zielgruppen, Branchen, Risiko."""
    d = {}
    d["challenge"] = inner_html(soup.select_one("#custom-challenge .command--content"))
    sol = soup.select_one("#custom-solution .command--content")
    d["solution"] = inner_html(sol)
    d["source_name"] = d["source_url"] = None
    if sol:
        link = sol.find("a")
        if link:
            d["source_name"] = text_of(link)
            d["source_url"] = link.get("href")
    d["benefits"] = inner_html(soup.select_one("#custom-benefits .command--content"))
    d["risk_class"] = text_of(soup.select_one("#custom-risk .risk--titel"))
    d["risk_reference"] = text_of(soup.select_one("#custom-risk .risk--reference"))
    d["risk_obligation"] = text_of(soup.select_one("#custom-obligation .risk--content"))
    d["detail_tags"] = ([("target", t) for t in box_tags(soup, "custom-target")] +
                        [("industry", t) for t in box_tags(soup, "custom-industries")])
    # Fallback, falls die Uebersicht keine Cluster/Domain/Prozess-Tags lieferte
    d["fallback_tags"] = ([("family", t) for t in box_tags(soup, "custom-family")] +
                          [("domain", t) for t in box_tags(soup, "custom-business")] +
                          [("process", t) for t in box_tags(soup, "custom-processes")])
    hero = soup.select_one(".case-background img")
    d["image_url_detail"] = hero["src"] if hero and hero.get("src") else None
    return d


def download_image(session, url, post_id):
    if not url:
        return None
    os.makedirs(IMG_DIR, exist_ok=True)
    # kleinere Variante bevorzugen (spart Platz, gleiche Bildaussage)
    candidates = [re.sub(r"-scaled\.jpg$", "-1280x720.jpg", url), url]
    ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
    fname = f"{post_id}{ext}"
    dest = os.path.join(IMG_DIR, fname)
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return fname
    for cand in candidates:
        try:
            r = session.get(cand, timeout=60)
            if r.ok and r.content:
                with open(dest, "wb") as fh:
                    fh.write(r.content)
                return fname
        except requests.RequestException:
            continue
    return None


def tag_id(cur, ttype, name):
    cur.execute("INSERT OR IGNORE INTO tags(type, name) VALUES (?,?)", (ttype, name))
    cur.execute("SELECT id FROM tags WHERE type=? AND name=?", (ttype, name))
    return cur.fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="de", choices=["de", "en"])
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--delay", type=float, default=0.6, help="Pause zwischen Requests (s)")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    cur = con.cursor()

    session = requests.Session()
    session.headers["User-Agent"] = UA

    print(f"[1/2] Uebersicht laden: {COLLECTION[args.lang]}")
    items = parse_list(get(session, COLLECTION[args.lang]))
    if args.limit:
        items = items[: args.limit]
    print(f"      {len(items)} Use Cases gefunden")

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for n, it in enumerate(items, 1):
        url = urljoin(BASE, it["url"])
        print(f"[2/2] {n}/{len(items)} {it['title'][:60]}")
        try:
            det = parse_detail(get(session, url))
        except requests.RequestException as exc:
            print(f"      !! uebersprungen: {exc}", file=sys.stderr)
            continue

        image_url = it["image_url"] or det["image_url_detail"]
        image_file = None if args.no_images else download_image(session, image_url, it["post_id"])

        cur.execute("""
            INSERT INTO use_cases (post_id, lang, slug, title, url, image_url, image_file,
                                   challenge, solution, benefits, source_name, source_url,
                                   risk_class, risk_reference, risk_obligation, scraped_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(post_id) DO UPDATE SET
                lang=excluded.lang, slug=excluded.slug, title=excluded.title, url=excluded.url,
                image_url=excluded.image_url, image_file=excluded.image_file,
                challenge=excluded.challenge, solution=excluded.solution, benefits=excluded.benefits,
                source_name=excluded.source_name, source_url=excluded.source_url,
                risk_class=excluded.risk_class, risk_reference=excluded.risk_reference,
                risk_obligation=excluded.risk_obligation, scraped_at=excluded.scraped_at
        """, (it["post_id"], args.lang, urlparse(url).path.strip("/").split("/")[-1],
              it["title"], url, image_url, image_file,
              det["challenge"], det["solution"], det["benefits"],
              det["source_name"], det["source_url"],
              det["risk_class"], det["risk_reference"], det["risk_obligation"], now))

        tags = it["tags"] or det["fallback_tags"]
        tags = tags + det["detail_tags"]
        cur.execute("DELETE FROM use_case_tags WHERE post_id=?", (it["post_id"],))
        for ttype, name in tags:
            if not name:
                continue
            cur.execute("INSERT OR IGNORE INTO use_case_tags(post_id, tag_id) VALUES (?,?)",
                        (it["post_id"], tag_id(cur, ttype, name)))
        con.commit()
        time.sleep(args.delay)

    # verwaiste Tags aufraeumen
    cur.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM use_case_tags)")
    con.commit()
    n_uc = cur.execute("SELECT COUNT(*) FROM use_cases").fetchone()[0]
    n_tag = cur.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    con.close()
    print(f"\nFertig: {n_uc} Use Cases, {n_tag} Tags -> {DB_PATH}")


if __name__ == "__main__":
    main()
