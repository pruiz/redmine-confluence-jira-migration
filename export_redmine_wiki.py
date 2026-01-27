import requests
import os
import re
from requests.utils import quote
from urllib.parse import urljoin

# === Configuration ===
PROJECT_ID = None  # Replace with your project identifier
API_KEY = None  # Replace with your Redmine API key
COOKIE = None  # Replace with your Redmine session cookie if needed
BASE_URL = None  # e.g., 'https://redmine.example.com'
HEADERS = None
SKIP_PAGES = set()

# === Create output folder ===
OUTPUTDIR = 'wiki_pages'

def download_file(url, path):
    try:
        resp = requests.get(url, headers=HEADERS)
        if resp.status_code == 200:
            with open(path, 'wb') as f:
                f.write(resp.content)
            print(f"   📎 Downloaded: {os.path.basename(path)}")
        else:
            print(f"   ⚠️ Failed to download {url} ({resp.status_code})")
            if args.fail_fast:
                exit()
    except Exception as ex:
        print(f"   ⚠️ Exception downloading {url}: {ex}")
        if args.fail_fast:
            exit()

def download_embedded_images(content, attachments, img_folder):
    # Find embedded images in Textile or HTML, not within <code>..</code> blocks
    textile_imgs = re.findall(r'!(?:\{[^\}]*\})?(^!\s+\.\s+)(?:\([^\)]*\))?!', content)
    #textile_imgs = re.findall(r'!(.+?)!', content)
    # Remove styling parameters nside the image reference (ie, remove anything between {})
    textile_imgs = [s.split("}", 1)[-1] for s in textile_imgs]
    # Remove optional title after the image URL (ie, remove anything after a parenthesis)
    textile_imgs = [s.split("(", 1)[0] for s in textile_imgs]
    html_imgs = re.findall(r'<img [^>]*src=[\'"]([^\'"]+)[\'"]', content)
    all_imgs = set(textile_imgs + html_imgs)
    if not all_imgs:
        return

    print(f"   🖼 Found {len(all_imgs)} embedded images, downloading to '{img_folder}'...")

    os.makedirs(img_folder, exist_ok=True)
    attachment_lookup = {att['filename']: att for att in attachments}
    attachment_lookup_lower = {att['filename'].lower(): att for att in attachments}

    for img in all_imgs:
        if img in attachment_lookup:
            img_url = attachment_lookup[img]['content_url']
        elif img.lower() in attachment_lookup_lower:
            img_url = attachment_lookup_lower[img.lower()]['content_url']
        elif img.startswith('http://') or img.startswith('https://'):
            img_url = img
        elif img.startswith('/'):
            img_url = urljoin(BASE_URL, img)
        elif len(img) == 0:
            print(f"   ⚠️ Empty image URL found, skipping.")
            continue
        else:
            print(f"   ⚠️ Could not resolve image '{img}' as attachment or absolute URL, skipping.")
            if args.fail_fast:
                exit()
            continue

        img_filename = os.path.basename(img.split('?')[0])
        img_path = os.path.join(img_folder, img_filename)
        download_file(img_url, img_path)

def fetch_pages_list():
    # try first fetching wiki list from API
    api_url = f'{BASE_URL}/projects/{PROJECT_ID}/wiki/index.json'
    response = requests.get(api_url, headers=HEADERS)

    print(f"🔍 Fetching wiki pages list from {api_url}...")

    if response.status_code == 200:
        try:
            data = response.json()
            wiki_pages = [page['title'] for page in data.get('wiki_pages', [])]
            return wiki_pages
        except Exception as e:
            print(f"⚠️ Could not parse JSON from API wiki index: {str(e)}, retrying with HTML parsing.")
            if args.fail_fast:
                exit()

    # fecht the list of wiki pages, from the html view, 
    # by extracting wiki names using reges
    response = requests.get(f'{BASE_URL}/projects/{PROJECT_ID}/wiki/index', headers=HEADERS)

    if response.status_code != 200:
        print(f"❌ Failed to fetch wiki index: {response.status_code}")
        print(response.text)
        exit()

    wiki_pages = re.findall(r'/projects/' + re.escape(PROJECT_ID) + r'/wiki/([^"\'>]+)"', response.text)
    return wiki_pages


# Parse arguments:
#  --redmine-url => Redmine instance URL
#  --redmine-user => Redmine user email
#  --redmine-token => Redmine API token
#  --redmine-cookie => Redmine session cookie
#  --redmine-project => Redmine project key
#  --output-dir => Output directory for wiki pages
#  --fail-fast => Fail fast on errors
#  --skip-pages => Comma-separated list of wiki pages to skip
def parse_args():
    import argparse
    parser = argparse.ArgumentParser(description='Export Redmine Wiki Pages with Metadata and Attachments')
    parser.add_argument('--redmine-url', type=str, required=True, help='Base URL of the Redmine instance')
    parser.add_argument('--redmine-token', type=str, help='Redmine API token')
    parser.add_argument('--redmine-cookie', type=str, help='Redmine session cookie')
    parser.add_argument('--redmine-project', type=str, required=True, help='Redmine project identifier')
    parser.add_argument('--output-dir', type=str, default='wiki_pages', help='Output directory for wiki pages')
    parser.add_argument('--fail-fast', action='store_true', help='Fail fast on errors')
    parser.add_argument('--skip-pages', type=str, help='Comma-separated list of wiki pages to skip')
    return parser.parse_args()

args = parse_args()
OUTPUTDIR = args.output_dir
BASE_URL = args.redmine_url.rstrip('/')
API_KEY = args.redmine_token
COOKIE = args.redmine_cookie
PROJECT_ID = args.redmine_project

if (not API_KEY) and (not COOKIE):
    print("❌ You must provide either an API token or a session cookie for authentication.")
    exit()

if COOKIE:
    HEADERS = {'Cookie': f"_redmine_session={COOKIE}"}
else:
    HEADERS = {'X-Redmine-API-Key': API_KEY}

if args.skip_pages:
    SKIP_PAGES = set([p.strip() for p in args.skip_pages.split(',') if p.strip()])

os.makedirs(OUTPUTDIR, exist_ok=True)

print(f"🚀 Starting export of wiki pages from project '{PROJECT_ID}' at '{BASE_URL}'...\n")

# === Step 1: Get the list of wiki pages ===
wiki_pages = fetch_pages_list()
print(f"📄 Found {len(wiki_pages)} wiki pages.")
# pretty print the list of pages
print("Pages:")
for page in wiki_pages:
    print(f" - {page}")

#raise SystemExit

# === Step 2: Download each wiki page and metadata ===
for page in wiki_pages:
    title = page

    if title in SKIP_PAGES:
        print(f"⏭ Skipping page: {title}")
        continue

    print(f"⬇ Downloading: {title}")

    # URL-encode the title for the request
    safe_title_for_url = quote(title, safe='')
    page_url = f'{BASE_URL}/projects/{PROJECT_ID}/wiki/{safe_title_for_url}.json?include=attachments'
    page_response = requests.get(page_url, headers=HEADERS)

    if page_response.status_code != 200:
        print(f"⚠️ Failed to fetch page '{title}': {page_response.status_code}")
        if args.fail_fast:
            exit()
        continue

    try:
        page_data = page_response.json().get('wiki_page', {})
    except Exception as e:
        print(f"⚠️ Could not parse JSON for page '{title}': {str(e)}")
        print("Response text was:", page_response.text[:300])
        if args.fail_fast:
            exit()
        continue

    # Metadata fields
    content = page_data.get('text', '')
    author = page_data.get('author', {}).get('name', 'Unknown')
    created_on = page_data.get('created_on', 'Unknown')
    updated_on = page_data.get('updated_on', 'Unknown')
    version = page_data.get('version', 'Unknown')
    comments = page_data.get('comments', '')
    parent = page_data.get('parent', {}).get('title', 'None')
    attachments = page_data.get('attachments', [])

    # Sanitize filename
    safe_title = re.sub(r'[<>:\"/\\|?*]', '_', title)
    json_file_path = os.path.join(OUTPUTDIR, f"{safe_title}.json")
    page_file_path = os.path.join(OUTPUTDIR, f"{safe_title}.txt")

    # Write raw JSON
    with open(json_file_path, 'w', encoding='utf-8') as f:
        f.write(page_response.text)

    # Write metadata + content
    with open(page_file_path, 'w', encoding='utf-8') as f:
        f.write(f"Title: {title}\n")
        f.write(f"Author: {author}\n")
        f.write(f"Created On: {created_on}\n")
        f.write(f"Last Updated: {updated_on}\n")
        f.write(f"Version: {version}\n")
        f.write(f"Parent Page: {parent}\n")
        f.write(f"Comments: {comments}\n")
        f.write(f"Attachments: {[att.get('filename') for att in attachments]}\n")
        f.write("\n---\n\n")
        f.write(content)

    # === Download attachments (if any) ===
    if attachments:
        attachment_folder = os.path.join(OUTPUTDIR, f"{safe_title}_attachments")
        os.makedirs(attachment_folder, exist_ok=True)
        for att in attachments:
            filename = att.get('filename')
            content_url = att.get('content_url')
            if not content_url or not filename:
                continue
            file_path = os.path.join(attachment_folder, filename)
            download_file(content_url, file_path)

    # === Download embedded images ===
    img_folder = os.path.join(OUTPUTDIR, f"{safe_title}_images")
    download_embedded_images(content, attachments, img_folder)

print(f"\n✅ Finished downloading {len(wiki_pages)} wiki pages into '{OUTPUTDIR}' folder (including embedded images and all attachments).")
