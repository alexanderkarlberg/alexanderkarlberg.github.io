#!/usr/bin/env python3
"""
inspire_to_jekyll_entities.py

- Fetch INSPIRE literature for an author and create Jekyll-style markdown + .bib files.
- Math delimiters are emitted using HTML numeric entity for backslash (&#92;) so templates
  that strip backslashes won't break MathJax delimiters.
- Full abstract is stored in 'excerpt' as a YAML block scalar.
- Usage:
    python inspire_to_jekyll_entities.py --author 1069015 --outdir ./_publications
"""
from datetime import datetime
import argparse
import os
import re
import time
import json
from urllib.parse import urlparse
import requests

API_BASE = "https://inspirehep.net/api"
HEADERS = {"User-Agent": "inspire-to-jekyll-script/entities/1.0 (+https://example.org/)"}

####################
# Helper utilities
####################

def normalize_author_input(s: str) -> str:
    """Accept either a numeric id or a full author URL and return the numeric id."""
    if not s:
        raise ValueError("Empty author input")
    if s.isdigit():
        return s
    try:
        p = urlparse(s)
        if "inspirehep.net" in p.netloc:
            parts = p.path.strip("/").split("/")
            for part in reversed(parts):
                if part.isdigit():
                    return part
    except Exception:
        pass
    raise ValueError("Could not determine author id from input: " + s)

def convert_latex_delimiters_entities(s: str) -> str:
    """
    Convert LaTeX delimiters to MathJax delimiters but encode the backslash
    as the HTML numeric entity &#92; so templates / filters won't strip it.
      $$ ... $$  -> &#92;[ ... &#92;]   (display math)
      $  ... $   -> &#92;( ... &#92;)   (inline math)
    Returns the converted string (no surrounding HTML tags).
    """
    if not s or "$" not in s:
        return s

    # replace display math $$...$$ first (non-greedy)
    def _disp(m):
        inner = m.group(1)
        return "&#92;[" + inner + "&#92;]"
    s = re.sub(r"\$\$(.+?)\$\$", _disp, s, flags=re.DOTALL)

    # then inline math $...$
    def _inline(m):
        inner = m.group(1)
        return "&#92;(" + inner + "&#92;)"
    s = re.sub(r"\$(.+?)\$", _inline, s, flags=re.DOTALL)

    return s

def slug_from_title(title: str, max_words=4):
    """Make a filesystem-friendly slug from the title (first max_words words)."""
    if not title:
        return "untitled"
    # remove inline math and punctuation for filename
    title_clean = re.sub(r"\$.*?\$", "", title)
    title_clean = re.sub(r"[^\w\s-]", "", title_clean)
    words = [w for w in re.split(r"\s+", title_clean.strip()) if w]
    slug_words = words[:max_words]
    slug = "-".join(w.lower() for w in slug_words)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "untitled"

####################
# Robust date parsing
####################

def recursive_find_first_date_string(obj):
    """
    Recursively search obj (dict/list/str) for the first YYYY-MM-DD or YYYY-MM.
    Preference: first YYYY-MM-DD found; otherwise first YYYY-MM found.
    Returns the matched string (YYYY-MM-DD or YYYY-MM) or None.
    """
    dd_pat = re.compile(r"(\d{4}-\d{2}-\d{2})")
    ym_pat = re.compile(r"(\d{4}-\d{2})(?!-)")  # avoid matching the start of a YYYY-MM-DD
    found_ym = None

    def walk(x):
        nonlocal found_ym
        if x is None:
            return None
        if isinstance(x, str):
            m = dd_pat.search(x)
            if m:
                return m.group(1)
            if found_ym is None:
                m2 = ym_pat.search(x)
                if m2:
                    found_ym = m2.group(1)
            return None
        if isinstance(x, dict):
            for v in x.values():
                r = walk(v)
                if r:
                    return r
        elif isinstance(x, (list, tuple)):
            for v in x:
                r = walk(v)
                if r:
                    return r
        return None

    exact = walk(obj)
    if exact:
        return exact
    return found_ym

def iso_date_from_record(meta: dict):
    """
    Extract best possible ISO date YYYY-MM-DD from record metadata.
    Priority:
      1) preprint_date (ISO or YYYY-MM)
      2) publication_info year/month/day (or year -> YYYY-01-01)
      3) created/updated/legacy_creation_date (ISO -> date; or embedded YYYY-MM-DD)
      4) recursive search for YYYY-MM-DD then YYYY-MM (convert YYYY-MM -> YYYY-MM-01)
      5) top-level year -> YYYY-01-01
      6) fallback: today's date
    """
    if not meta:
        return datetime.today().date().isoformat()

    # 1) preprint_date
    pre = meta.get("preprint_date")
    if pre:
        s = str(pre).strip()
        try:
            d = datetime.fromisoformat(s)
            return d.date().isoformat()
        except Exception:
            pass
        m_ym = re.match(r"^(\d{4}-\d{2})$", s)
        if m_ym:
            return f"{m_ym.group(1)}-01"
        m_any = re.search(r"(\d{4}-\d{2}-\d{2})", s)
        if m_any:
            return m_any.group(1)

    # 2) publication_info
    pubinfo = meta.get("publication_info") or []
    if pubinfo:
        info0 = pubinfo[0]
        year = info0.get("year") or meta.get("year")
        month = info0.get("month")
        day = info0.get("day")
        try:
            if year:
                y = int(str(year)[:4])
                if month and str(month).isdigit():
                    m = int(month)
                    d = int(day) if day and str(day).isdigit() else 1
                    return f"{y:04d}-{m:02d}-{d:02d}"
                return f"{y:04d}-01-01"
        except Exception:
            pass

    # 3) created/updated/legacy_creation_date
    for key in ("created", "updated", "legacy_creation_date"):
        val = meta.get(key)
        if val:
            s = str(val)
            try:
                dt = datetime.fromisoformat(s)
                return dt.date().isoformat()
            except Exception:
                m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
                if m:
                    return m.group(1)
                m2 = re.match(r"^(\d{4}-\d{2})$", s)
                if m2:
                    return f"{m2.group(1)}-01"

    # 4) recursive search anywhere
    found = recursive_find_first_date_string(meta)
    if found:
        if re.match(r"^\d{4}-\d{2}$", found):
            return f"{found}-01"
        return found

    # 5) top-level year
    if meta.get("year"):
        try:
            y = int(str(meta.get("year"))[:4])
            return f"{y:04d}-01-01"
        except Exception:
            pass

    # 6) fallback
    return datetime.today().date().isoformat()

####################
# Metadata pickers
####################

def pick_abstract(meta: dict):
    abslist = meta.get("abstracts") or []
    if abslist and isinstance(abslist, list):
        for a in abslist:
            if isinstance(a, dict) and a.get("value"):
                text = a.get("value").strip()
                if text:
                    return text
    # fallback
    if meta.get("description"):
        return meta.get("description")
    return ""

def pick_venue(meta: dict):
    pubinfo = meta.get("publication_info") or []
    if pubinfo:
        info0 = pubinfo[0]
        journal_title = info0.get("journal_title") or info0.get("journal")
        if journal_title:
            return journal_title
        if info0.get("journal") and isinstance(info0.get("journal"), dict):
            return info0.get("journal").get("title") or info0.get("journal").get("name") or ""
    for k in ("imprint", "pubinfo_freetext"):
        if meta.get(k):
            return meta.get(k)
    return ""

def pick_pdf_url(record_json: dict):
    meta = record_json.get("metadata") or {}
    for key in ("urls", "links", "documents", "files"):
        items = meta.get(key) or []
        for it in items:
            if isinstance(it, dict):
                for candidate_key in ("value", "url", "href", "link"):
                    url = it.get(candidate_key)
                    if url and isinstance(url, str) and url.lower().endswith(".pdf"):
                        return url
    # arXiv fallback
    arxiv_eprints = []
    if meta.get("arxiv_eprints"):
        for a in meta.get("arxiv_eprints"):
            if isinstance(a, dict) and a.get("value"):
                arxiv_eprints.append(a.get("value"))
    else:
        ids = meta.get("identifiers") or []
        for i in ids:
            if isinstance(i, dict):
                val = i.get("value", "")
                if "arXiv" in (i.get("schema", "") or "") or re.search(r"^\d{4}\.\d{4,5}", str(val)):
                    arxiv_eprints.append(val)
                elif isinstance(val, str) and "arXiv:" in val:
                    arxiv_eprints.append(val.split("arXiv:")[-1])
    if arxiv_eprints:
        aid = arxiv_eprints[0]
        aid_clean = re.sub(r"v\d+$", "", str(aid))
        return f"https://arxiv.org/pdf/{aid_clean}.pdf"
    return ""

def is_published(meta: dict) -> bool:
    if not meta:
        return False
    pubinfo = meta.get("publication_info") or []
    if not pubinfo:
        return False
    info0 = pubinfo[0]
    for key in ("journal_title", "journal", "publisher", "imprint"):
        val = info0.get(key) or meta.get(key)
        if val:
            return True
    return False

####################
# I/O: bib + markdown
####################

def download_bib(outdir, date_iso, slug, recid):
    bib_filename = f"{date_iso}-{slug}.bib"
    bib_path = os.path.join(outdir, bib_filename)
    bib_url = f"{API_BASE}/literature/{recid}?format=bibtex"
    try:
        r = requests.get(bib_url, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            with open(bib_path, "wb") as fh:
                fh.write(r.content)
            print("BIB:", bib_path)
            return bib_path, bib_url
        else:
            print(f"Warning: could not download bib ({r.status_code}) from {bib_url}")
    except Exception as e:
        print("Error downloading bib:", e)
    return None, bib_url

def write_markdown(outdir, date_iso, slug, meta, recid, pdf_url, bib_path):
    """
    Writes markdown with YAML front matter:
      - title as double-quoted string (backslashes and quotes escaped)
      - excerpt as YAML block scalar '|' (full abstract preserved)
      - math delimiters emitted using HTML numeric entity &#92; to protect them
    """
    filename = f"{date_iso}-{slug}.md"
    path = os.path.join(outdir, filename)

    # Title extraction
    title = ""
    if meta.get("title"):
        title = meta.get("title")
    elif meta.get("titles"):
        try:
            t0 = meta["titles"][0]
            title = t0.get("title") if isinstance(t0, dict) else str(t0)
        except Exception:
            title = ""
    if not title:
        title = "Untitled"

    # Convert delimiters to entity-escaped backslashes so templates won't strip them
    title_conv = convert_latex_delimiters_entities(title)
    abstract = pick_abstract(meta) or ""
    abstract_conv = convert_latex_delimiters_entities(abstract)

    # Determine category & venue
    if is_published(meta):
        category = "manuscripts"
        venue = pick_venue(meta) or ""
    else:
        category = "reports"
        venue = "arXiv"

    permalink = f"/publication/{date_iso}-{slug}"
    paperurl = pdf_url or ""
    bibtexurl = bib_path.replace("\\", "/") if bib_path else ""

    # Escape backslashes and double quotes for double-quoted YAML scalars
    title_for_yaml = title_conv.replace("\\", "\\\\").replace('"', '\\"')
    venue_for_yaml = (venue or "").replace("\\", "\\\\").replace('"', '\\"')

    # Write file
    with open(path, "w", encoding="utf-8") as f:
        f.write("---\n")
        f.write(f'title: "{title_for_yaml}"\n')
        f.write("collection: publications\n")
        f.write(f"category: {category}\n")
        f.write(f'permalink: "{permalink}"\n')
        # excerpt as block scalar to preserve entities and newlines
        f.write("excerpt: |\n")
        if abstract_conv:
            for line in abstract_conv.splitlines():
                f.write(f"  {line}\n")
        else:
            f.write("  \n")
        f.write(f"date: {date_iso}\n")
        f.write(f'venue: "{venue_for_yaml}"\n')
        f.write(f'paperurl: "{paperurl}"\n')
        f.write(f'bibtexurl: "{bibtexurl}"\n')
        f.write("---\n\n")
        # Body (optional): include abstract again
        if abstract_conv:
            f.write("__Abstract__: " + abstract_conv + "\n")
    print("WROTE:", path)
    return path

####################
# INSPIRE fetching
####################

def fetch_author_publications(author_id: str, size=500):
    session = requests.Session()
    session.headers.update(HEADERS)
    queries = [
        f"authors.recid:{author_id}",
        f"authors:{author_id}",
        f"author:{author_id}",
    ]
    for q in queries:
        params = {"q": q, "size": size, "sort": "mostrecent"}
        url = f"{API_BASE}/literature"
        print("Querying:", url, "params:", params)
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                data = r.json()
                hits = data.get("hits", {}).get("hits") or data.get("hits") or data.get("data") or []
                if hits:
                    return hits
            else:
                print("Query returned status", r.status_code, "for q=", q)
        except Exception as e:
            print("Error querying INSPIRE:", e)
    return []

def extract_recid(hit: dict):
    if isinstance(hit, dict):
        if hit.get("control_number"):
            return str(hit.get("control_number"))
        if hit.get("id"):
            idv = hit.get("id")
            m = re.search(r"/literature/(\d+)", str(idv))
            if m:
                return m.group(1)
            if str(idv).isdigit():
                return str(idv)
        meta = hit.get("metadata") or {}
        if meta.get("control_number"):
            return str(meta.get("control_number"))
    return None

####################
# CLI / main
####################

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--author", help="INSPIRE author id (digits)")
    p.add_argument("--author-url", help="INSPIRE author URL")
    p.add_argument("--outdir", default="./publications", help="output directory")
    p.add_argument("--size", type=int, default=500, help="max number of publications to fetch")
    p.add_argument("--dry-run", action="store_true", help="don't write files, just print plan")
    args = p.parse_args()

    if not args.author and not args.author_url:
        p.error("Provide --author or --author-url")

    author_id = args.author or normalize_author_input(args.author_url)
    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    hits = fetch_author_publications(author_id, size=args.size)
    if not hits:
        print("No publications found for author id", author_id)
        return

    print(f"Found {len(hits)} hits (may include duplicates). Processing...")

    for i, hit in enumerate(hits, start=1):
        recid = extract_recid(hit)
        if not recid:
            recid = extract_recid(hit.get("_source", {}) if isinstance(hit, dict) else {})
        if not recid:
            print("Skipping: cannot determine recid for hit #", i)
            continue

        rec_url = f"{API_BASE}/literature/{recid}"
        try:
            r = requests.get(rec_url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                print("Warning: could not fetch full record", recid, "status", r.status_code)
                record_json = hit if isinstance(hit, dict) else {}
            else:
                record_json = r.json()
        except Exception as e:
            print("Warning: exception fetching record", recid, e)
            record_json = hit if isinstance(hit, dict) else {}

        meta = record_json.get("metadata") or record_json.get("_source") or record_json

        # Title
        title = ""
        if meta.get("title"):
            title = meta.get("title")
        elif meta.get("titles"):
            try:
                t0 = meta["titles"][0]
                if isinstance(t0, dict):
                    title = t0.get("title", "")
                else:
                    title = str(t0)
            except Exception:
                title = ""
        if not title:
            title = "Untitled"

        slug = slug_from_title(title)
        date_iso = iso_date_from_record(meta)
        pdf_url = pick_pdf_url(record_json)

        bib_path, bib_remote_url = None, None
        if not args.dry_run:
            bib_path, bib_remote_url = download_bib(outdir, date_iso, slug, recid)
        else:
            bib_remote_url = f"{API_BASE}/literature/{recid}?format=bibtex"
            print("(dry-run) Would download bib from:", bib_remote_url)

        if not args.dry_run:
            write_markdown(outdir, date_iso, slug, meta, recid, pdf_url or "", bib_path or "")
        else:
            print(f"(dry-run) Would write: {date_iso}-{slug}.md and .bib")

        # be polite with the API
        time.sleep(0.25)

if __name__ == "__main__":
    main()
