#!/usr/bin/env python3
"""
generate_talk_md.py

Interactive generator for talk markdown files.

Saves files as:
  YYYY-MM-DD-first-four-words-hyphenated.md

Front matter example produced:
---
title: "Talk 1 on Relevant Topic in Your Field"
collection: talks
type: "Talk"
permalink: /talks/2012-03-01-talk-1
venue: "UC San Francisco, Department of Testing"
date: 2012-03-01
location: "San Francisco, CA, USA"
slidesurl: "https://..."
---

Description...
"""
from datetime import datetime
import os
import re
import argparse
import sys

def slug_from_title(title: str, max_words=4):
    if not title:
        return "untitled"
    # remove inline math or $...$ segments for filename safety, then punctuation
    title_clean = re.sub(r"\$.*?\$", "", title)
    title_clean = re.sub(r"[^\w\s-]", "", title_clean)
    words = [w for w in re.split(r"\s+", title_clean.strip()) if w]
    slug_words = words[:max_words]
    slug = "-".join(w.lower() for w in slug_words)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "untitled"

def safe_filename_component(s: str):
    # keep only safe filename characters (alphanumeric, -, _ , .)
    s2 = re.sub(r"[^\w\-.]", "_", s)
    return s2

def prompt_multiline(prompt="Enter description (end with a line 'END'):\n"):
    print(prompt)
    print("(Type your description. On a new line type only END and press Enter to finish.)")
    lines = []
    while True:
        try:
            ln = input()
        except EOFError:
            # allow Ctrl-D to finish as well
            break
        if ln.strip() == "END":
            break
        lines.append(ln)
    return "\n".join(lines).rstrip()

def escape_for_double_quoted_yaml(s: str) -> str:
    # escape backslashes and double quotes for YAML double-quoted scalars
    if s is None:
        return ""
    return s.replace("\\", "\\\\").replace('"', '\\"')

def main():
    p = argparse.ArgumentParser(description="Generate a Jekyll talk markdown file interactively.")
    p.add_argument("--outdir", "-o", default="./_talks", help="Output directory for generated .md files")
    p.add_argument("--dry-run", action="store_true", help="Print the would-be filename and front matter without writing")
    args = p.parse_args()

    outdir = args.outdir
    os.makedirs(outdir, exist_ok=True)

    # 1) Date
    while True:
        date_str = input("Date of talk (YYYY-MM-DD): ").strip()
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            break
        except Exception:
            print("Invalid date format. Please enter date as YYYY-MM-DD (e.g. 2023-11-05).")

    # 2) Title
    title = input("Title of talk: ").strip()
    while not title:
        title = input("Title cannot be empty. Title of talk: ").strip()

    # 3) Type
    typeinfo = input("Type of talk (seminar, invited talk, etc): ").strip()
    while not typeinfo:
        typeinfo = input("Type cannot be empty. Type of talk: ").strip()

    # 4) Venue
    venue = input("Venue (e.g. conference or department name): ").strip()

    # 5) Location
    location = input("Location (e.g. City, Country): ").strip()

    # 6) Slides URL
    slidesurl = input("Link to slides (URL) [optional]: ").strip()

    # 7) Description (multiline)
    description = prompt_multiline()

    # Build slug and filename
    slug = slug_from_title(title, max_words=4)
    slug_safe = safe_filename_component(slug)
    filename = f"{date_str}-{slug_safe}.md"
    filepath = os.path.join(outdir, filename)

    permalink = f"/talks/{date_str}-{slug_safe}"

    # Prepare front matter values; escape for double-quoted YAML scalars
    title_yaml = escape_for_double_quoted_yaml(title)
    type_yaml = escape_for_double_quoted_yaml(typeinfo)
    venue_yaml = escape_for_double_quoted_yaml(venue)
    location_yaml = escape_for_double_quoted_yaml(location)
    slidesurl_yaml = escape_for_double_quoted_yaml(slidesurl)

    # Compose front matter
    front_lines = []
    front_lines.append("---")
    front_lines.append(f'title: "{title_yaml}"')
    front_lines.append("collection: talks")
    front_lines.append(f'type: "{type_yaml}"')
    front_lines.append(f'permalink: {permalink}')
    if venue:
        front_lines.append(f'venue: "{venue_yaml}"')
    front_lines.append(f"date: {date_str}")
    if location:
        front_lines.append(f'location: "{location_yaml}"')
    if slidesurl:
        front_lines.append(f'slidesurl: "{slidesurl_yaml}"')
    front_lines.append("---")
    front = "\n".join(front_lines)

    # YAML block scalar for body (preserve backslashes/newlines)
    body = description if description else ""

    if args.dry_run:
        print("\nDRY RUN - would write:", filepath)
        print("\n--- FRONT MATTER ---")
        print(front)
        print("\n--- BODY ---")
        print(body[:1000] + ("..." if len(body) > 1000 else ""))
        return

    # If file exists, confirm overwrite
    if os.path.exists(filepath):
        yn = input(f"File {filepath} already exists. Overwrite? [y/N]: ").strip().lower()
        if yn not in ("y", "yes"):
            print("Aborted. No file written.")
            return

    # Write file
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write(front + "\n\n")
        if body:
            # write as plain markdown body (not in front matter)
            fh.write(body + "\n")
        else:
            fh.write("\n")
    print("WROTE:", filepath)

if __name__ == "__main__":
    main()
