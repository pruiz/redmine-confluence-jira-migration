# Redmine to Jira & Confluence Migration Toolkit

This toolkit helps you export issues and wiki content from Redmine and import them into the Cloud version of Jira and Confluence, while preserving metadata, formatting, attachments, and hierarchy. You need to have access to create API keys for both Redmine and Atlassian in order to use this script.

---

## Scripts Overview

| Script | Purpose |
|--------|---------|
| `export_redmine_issues.py` | Export all Redmine issues (including closed) with metadata, comments, and attachments |
| `export_redmine_wiki.py`   | Export Redmine wiki pages with hierarchy, metadata, embedded images, and attachments |
| `import_to_jira.py`        | Create Jira issues from exported Redmine issues, preserving formatting and attaching long comments/metadata |
| `import_to_confluence.py`  | Recreate Redmine wiki hierarchy in Confluence with full content, attachments, and image macros |

---

## Requirements

Install Python dependencies:

```bash
pip install requests atlassian pyyaml python-dotenv
```

For textile to HTML/Markdown conversion, install [Pandoc](https://pandoc.org):

```bash
# Windows (choco), Mac (brew), or Linux
choco install pandoc  # or
brew install pandoc   # or
apt install pandoc
```

---

## Script Details

### 1. `export_redmine_issues.py`

Update the configuration section:

~~~
# === Configuration ===
project_id = '%PROJECT%'
api_key = '%API-KEY%'
base_url = '%SITE-URL%'
headers = {'X-Redmine-API-Key': api_key}

output_folder = 'redmine_issues'
os.makedirs(output_folder, exist_ok=True)
~~~

Exports Redmine issues (including closed) with full metadata, comments, and attachments.

- Saves each issue as:
  - `issue_<ID>.json` (raw)
  - `issue_<ID>.txt` (readable)
  - `issue_<ID>_comments.txt` (comments)
  - `issue_<ID>_attachments/` (attachments)

Handles pagination, includes journals/comments, and downloads all attachments.

> Configure `api_key`, `project_id`, and `base_url` inside the script.

---

### 2. `export_redmine_wiki.py`

Update the configuration section:

~~~
# === Configuration ===
project_id = '%PROJECT%'
api_key = '%API-KEY%'
base_url = 'https://%SITE-URL%'
headers = {'X-Redmine-API-Key': api_key}

# === Create output folder ===
output_folder = 'wiki_pages'
os.makedirs(output_folder, exist_ok=True)
~~~

Exports all wiki pages from a Redmine project.

- Saves each page with:
  - Title, author, version, creation/update dates
  - Hierarchy (parent/child)
  - Attachments and embedded images

Files are saved as `.txt` with metadata headers. Images are extracted from `<img>` or Textile `!filename!` references.

---

### 3. `import_to_jira.py`

Update the configuration section:

~~~
# === Jira configuration ===
JIRA_URL = "https://DOMAIN.atlassian.net"
JIRA_USER = "YOUR EMAIL"
JIRA_API_TOKEN = "YOUR API KEY"
JIRA_PROJECT_KEY = "KEY"  # Your target Jira project key

# === Source Redmine issues (txt files for comments, description, and JSON of the issue)===
redmine_issues_folder = r"LOCATION OF EXPORTED FILES"
~~~

Creates Jira issues using ADF (Atlassian Document Format) for formatting.

- Pulls from exported JSON files
- Preserves:
  - Description formatting
  - Author/timestamp
  - Redmine metadata
- Attaches:
  - Full original issue `.txt` and `.comments.txt` (always)
- Handles:
  - Long descriptions by attaching content instead of embedding
  - Jira field mappings (e.g. Priority, Assignee, Tracker, Labels)
  - Custom metadata as table in description
  - Comments as ADF blocks

You can define Jira credentials in `.env` or directly in script.

---

### 4. `import_to_confluence.py`

Update the configuration section:

~~~
# === Confluence configuration ===
CONFLUENCE_URL = "https://REPLACEWITHYOURS.atlassian.net/wiki"
CONFLUENCE_USER = "REPLACEWITHYOURS"
CONFLUENCE_API_TOKEN = "REPLACEWITHYOURS"
CONFLUENCE_SPACE_KEY = "RA""  # Your target space key

# === Local wiki export location ===
wiki_dir = r"LOCATION OF DOWNLOADED WIKI"  # Use raw string to avoid escape issues
~~~

Recreates Redmine wiki in Confluence using the REST API.

- Preserves:
  - Page hierarchy
  - Attachments and image macros
  - HTML conversion via Pandoc

Parses metadata from headers. Supports uploading images and embedding them using `<ac:image>` macros.

Automatically skips pages already created, handles empty pages or missing parents, and recovers from errors.

---

## Usage Example

```bash
# Step 1: Export Redmine data
python export_redmine_issues.py
python export_redmine_wiki.py

# Step 2: Import into Jira & Confluence
python import_to_jira.py
python import_to_confluence.py
```

---

## Notes

- All metadata is preserved using tables in descriptions (e.g., Redmine ID, status, author)
- Handles `CONTENT_LIMIT_EXCEEDED` errors by attaching content as `.txt` files
- Errors such as duplicate attachments or pages are skipped gracefully
- Ensure user permissions are sufficient to create content in Jira and Confluence

---

## License

MIT License — Free for use and modification.



