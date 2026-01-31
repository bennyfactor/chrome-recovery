#!/usr/bin/env python3
"""
ChromeRecovery — Extract tabs, history, and bookmarks from a Chrome profile folder.
Outputs a human-readable HTML dashboard and a browser-importable bookmarks file.
"""

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import datetime
import html as html_module
import shutil

# ---------------------------------------------------------------------------
# Helpers: macOS native dialogs via osascript
# ---------------------------------------------------------------------------

def show_alert(title, message, icon="caution"):
    """Show a native macOS alert dialog."""
    script = f'''
    display dialog "{message}" with title "{title}" buttons {{"OK"}} default button "OK" with icon {icon}
    '''
    subprocess.run(["osascript", "-e", script], capture_output=True)


def pick_folder():
    """Open a native Finder folder-picker and return the selected path (or None)."""
    script = '''
    set folderPath to POSIX path of (choose folder with prompt "Select your Chrome profile folder")
    return folderPath
    '''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def reveal_in_finder(path):
    """Open a Finder window showing the given path."""
    subprocess.run(["open", str(path)], capture_output=True)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

EXPECTED_FILES = ["Bookmarks", "History"]
SESSION_FILES = ["Current Session", "Current Tabs", "Last Session", "Last Tabs"]


def validate_profile(profile_path):
    """Check that the folder looks like a Chrome profile. Returns (ok, missing)."""
    missing = [f for f in EXPECTED_FILES if not (profile_path / f).exists()]
    return len(missing) == 0, missing


# ---------------------------------------------------------------------------
# Bookmarks extraction
# ---------------------------------------------------------------------------

def extract_bookmarks(profile_path):
    """Parse Chrome's Bookmarks JSON. Returns a tree structure."""
    bookmarks_file = profile_path / "Bookmarks"
    if not bookmarks_file.exists():
        return None
    with open(bookmarks_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("roots", {})


def walk_bookmarks(node, depth=0):
    """Recursively walk bookmark tree, yielding (depth, type, name, url)."""
    if node.get("type") == "folder":
        yield (depth, "folder", node.get("name", "Untitled"), None)
        for child in node.get("children", []):
            yield from walk_bookmarks(child, depth + 1)
    elif node.get("type") == "url":
        yield (depth, "url", node.get("name", "Untitled"), node.get("url", ""))


# ---------------------------------------------------------------------------
# History extraction
# ---------------------------------------------------------------------------

def extract_history(profile_path, limit=5000):
    """
    Read Chrome's History SQLite database.
    Returns list of (url, title, last_visit_time_str).
    We copy the DB to a temp location to avoid locking issues.
    """
    history_file = profile_path / "History"
    if not history_file.exists():
        return None

    # Copy to temp to avoid SQLite lock issues
    tmp_path = pathlib.Path("/tmp/chrome_recovery_history_copy")
    shutil.copy2(history_file, tmp_path)

    try:
        conn = sqlite3.connect(str(tmp_path))
        cursor = conn.cursor()
        cursor.execute("""
            SELECT url, title, last_visit_time
            FROM urls
            ORDER BY last_visit_time DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    CHROME_EPOCH = datetime.datetime(1601, 1, 1)
    results = []
    for url, title, timestamp in rows:
        try:
            dt = CHROME_EPOCH + datetime.timedelta(microseconds=timestamp)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            time_str = "Unknown"
        results.append((url, title or "", time_str))
    return results


# ---------------------------------------------------------------------------
# Tabs extraction (SNSS session files via vendored ccl_chromium_reader)
# ---------------------------------------------------------------------------

def extract_tabs(profile_path):
    """
    Try to read open tabs from Chrome session files.
    Returns list of (url, title) or None on failure.
    """
    # Add the directory containing our vendored library to the path
    resources_dir = pathlib.Path(__file__).parent
    if str(resources_dir) not in sys.path:
        sys.path.insert(0, str(resources_dir))

    try:
        from ccl_chromium_reader.ccl_chromium_snss2 import SnssFile, SnssFileType, NavigationEntry
    except ImportError as e:
        print(f"Could not import SNSS reader: {e}", file=sys.stderr)
        return None

    tabs = []
    seen_urls = set()

    # Try session files in order of preference
    session_files = [
        ("Current Session", SnssFileType.Session),
        ("Last Session", SnssFileType.Session),
        ("Current Tabs", SnssFileType.Tab),
        ("Last Tabs", SnssFileType.Tab),
    ]

    for filename, file_type in session_files:
        filepath = profile_path / filename
        if not filepath.exists():
            continue
        try:
            with open(filepath, "rb") as f:
                snss = SnssFile(file_type, f)
                for entry in snss:
                    if isinstance(entry, NavigationEntry):
                        url = entry.url
                        title = entry.title or ""
                        if url and url not in seen_urls and not url.startswith("chrome://"):
                            seen_urls.add(url)
                            tabs.append((url, title))
        except Exception as e:
            print(f"Warning: Could not read {filename}: {e}", file=sys.stderr)
            continue

    return tabs if tabs else None


# ---------------------------------------------------------------------------
# HTML dashboard output
# ---------------------------------------------------------------------------

def esc(text):
    return html_module.escape(str(text))


def generate_dashboard(tabs, bookmarks_roots, history, output_path):
    """Generate the human-readable HTML dashboard."""

    parts = []
    parts.append("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Chrome Recovery</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    max-width: 900px; margin: 40px auto; padding: 0 20px;
    color: #333; background: #fafafa;
  }
  h1 { font-size: 28px; margin-bottom: 8px; }
  .subtitle { color: #888; margin-bottom: 32px; }
  h2 { font-size: 20px; margin: 32px 0 16px; padding-bottom: 8px; border-bottom: 2px solid #ddd; }
  .tab-list, .history-list { list-style: none; }
  .tab-list li, .history-list li {
    padding: 8px 0; border-bottom: 1px solid #eee;
  }
  .tab-list a, .history-list a, .bookmark-link {
    color: #1a73e8; text-decoration: none;
  }
  .tab-list a:hover, .history-list a:hover, .bookmark-link:hover {
    text-decoration: underline;
  }
  .url-display { color: #888; font-size: 12px; display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .timestamp { color: #888; font-size: 12px; margin-left: 8px; }
  .folder { font-weight: 600; margin-top: 12px; padding: 4px 0; }
  .bookmark-item { padding: 3px 0; }
  .indent-1 { padding-left: 20px; }
  .indent-2 { padding-left: 40px; }
  .indent-3 { padding-left: 60px; }
  .indent-4 { padding-left: 80px; }
  .indent-5 { padding-left: 100px; }
  .section-count { color: #888; font-weight: normal; font-size: 14px; }
  .empty-note { color: #999; font-style: italic; padding: 12px 0; }
  nav { margin-bottom: 24px; }
  nav a { margin-right: 16px; color: #1a73e8; text-decoration: none; font-weight: 500; }
  nav a:hover { text-decoration: underline; }
</style>
</head>
<body>
<h1>Chrome Recovery</h1>
<p class="subtitle">Recovered from Chrome profile data</p>
<nav>""")

    # Navigation links
    if tabs is not None:
        parts.append('<a href="#tabs">Open Tabs</a>')
    parts.append('<a href="#bookmarks">Bookmarks</a>')
    if history is not None:
        parts.append('<a href="#history">History</a>')
    parts.append("</nav>")

    # --- Tabs section ---
    if tabs is not None:
        parts.append(f'<h2 id="tabs">Open Tabs <span class="section-count">({len(tabs)})</span></h2>')
        if tabs:
            parts.append('<ul class="tab-list">')
            for url, title in tabs:
                display_title = esc(title) if title else esc(url)
                parts.append(f'<li><a href="{esc(url)}">{display_title}</a>'
                             f'<span class="url-display">{esc(url)}</span></li>')
            parts.append("</ul>")
        else:
            parts.append('<p class="empty-note">No open tabs found.</p>')
    else:
        parts.append('<h2 id="tabs">Open Tabs</h2>')
        parts.append('<p class="empty-note">Could not recover open tabs from session files.</p>')

    # --- Bookmarks section ---
    parts.append('<h2 id="bookmarks">Bookmarks</h2>')
    if bookmarks_roots:
        bookmark_count = 0
        bookmark_parts = []
        for root_name, root_node in bookmarks_roots.items():
            if root_name in ("synced", "sync_transaction_version"):
                continue
            for depth, node_type, name, url in walk_bookmarks(root_node):
                indent = min(depth, 5)
                if node_type == "folder":
                    bookmark_parts.append(f'<div class="folder indent-{indent}">{esc(name)}</div>')
                else:
                    bookmark_count += 1
                    bookmark_parts.append(
                        f'<div class="bookmark-item indent-{indent}">'
                        f'<a class="bookmark-link" href="{esc(url)}">{esc(name)}</a></div>'
                    )
        parts.append(f'<p class="section-count">{bookmark_count} bookmarks</p>')
        parts.extend(bookmark_parts)
    else:
        parts.append('<p class="empty-note">No bookmarks found.</p>')

    # --- History section ---
    if history is not None:
        parts.append(f'<h2 id="history">Browsing History <span class="section-count">({len(history)})</span></h2>')
        if history:
            parts.append('<ul class="history-list">')
            for url, title, time_str in history:
                display_title = esc(title) if title else esc(url)
                parts.append(f'<li><a href="{esc(url)}">{display_title}</a>'
                             f'<span class="timestamp">{esc(time_str)}</span>'
                             f'<span class="url-display">{esc(url)}</span></li>')
            parts.append("</ul>")
        else:
            parts.append('<p class="empty-note">No history entries found.</p>')
    else:
        parts.append('<h2 id="history">Browsing History</h2>')
        parts.append('<p class="empty-note">Could not recover browsing history.</p>')

    parts.append("</body></html>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


# ---------------------------------------------------------------------------
# Netscape bookmarks export (importable)
# ---------------------------------------------------------------------------

def generate_importable_bookmarks(bookmarks_roots, output_path):
    """Generate a Netscape-format bookmarks HTML file for browser import."""
    parts = []
    parts.append("<!DOCTYPE NETSCAPE-Bookmark-file-1>")
    parts.append("<!-- This is an automatically generated file.")
    parts.append("     It will be read and overwritten.")
    parts.append("     DO NOT EDIT! -->")
    parts.append('<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">')
    parts.append("<TITLE>Bookmarks</TITLE>")
    parts.append("<H1>Bookmarks</H1>")
    parts.append("<DL><p>")

    if bookmarks_roots:
        for root_name, root_node in bookmarks_roots.items():
            if root_name in ("synced", "sync_transaction_version"):
                continue
            _write_netscape_node(root_node, parts, indent=1)

    parts.append("</DL><p>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


def _write_netscape_node(node, parts, indent=0):
    prefix = "    " * indent
    if node.get("type") == "folder":
        parts.append(f'{prefix}<DT><H3>{esc(node.get("name", "Untitled"))}</H3>')
        parts.append(f"{prefix}<DL><p>")
        for child in node.get("children", []):
            _write_netscape_node(child, parts, indent + 1)
        parts.append(f"{prefix}</DL><p>")
    elif node.get("type") == "url":
        url = node.get("url", "")
        name = node.get("name", "Untitled")
        parts.append(f'{prefix}<DT><A HREF="{esc(url)}">{esc(name)}</A>')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Pick folder
    folder = pick_folder()
    if not folder:
        sys.exit(0)  # User cancelled

    profile_path = pathlib.Path(folder)

    # Validate
    ok, missing = validate_profile(profile_path)
    if not ok:
        show_alert(
            "Not a Chrome Profile",
            f"That folder doesn't look like a Chrome profile. "
            f"We expected to find: {', '.join(missing)}. "
            f"Make sure you selected the folder that contains your Chrome data "
            f"(usually named 'Default' or 'Profile 1')."
        )
        sys.exit(1)

    # Extract data
    bookmarks_roots = extract_bookmarks(profile_path)
    history = extract_history(profile_path)
    tabs = extract_tabs(profile_path)

    # Determine output location
    desktop = pathlib.Path.home() / "Desktop"
    if not desktop.exists():
        desktop = pathlib.Path.home()

    dashboard_path = desktop / "Chrome Recovery.html"
    bookmarks_path = desktop / "Chrome Bookmarks.html"

    # Generate outputs
    generate_dashboard(tabs, bookmarks_roots, history, dashboard_path)

    if bookmarks_roots:
        generate_importable_bookmarks(bookmarks_roots, bookmarks_path)

    # Summary message
    recovered = []
    if tabs:
        recovered.append(f"{len(tabs)} open tabs")
    if bookmarks_roots:
        recovered.append("bookmarks")
    if history:
        recovered.append(f"{len(history)} history entries")

    if recovered:
        summary = "Recovered: " + ", ".join(recovered) + "."
    else:
        summary = "No data could be recovered from that profile."

    files_note = "Files saved to your Desktop."
    if not bookmarks_roots:
        files_note = "Dashboard saved to your Desktop."

    show_alert("Recovery Complete", f"{summary} {files_note}", icon="note")
    reveal_in_finder(desktop)


if __name__ == "__main__":
    main()
