import os
import re
import subprocess
import requests
import textile
import html as html_stdlib
from pprint import pprint
from atlassian import Confluence
from bs4 import BeautifulSoup
from bs4.element import CData

# === Confluence configuration ===
CONFLUENCE_URL = None
CONFLUENCE_USER = None
CONFLUENCE_API_TOKEN = None
CONFLUENCE_SPACE_ID = None
CONFLUENCE_SPACE_KEY = None
CONFLUENCE_PARENT_FOLDER = None
CONFLUENCE_PAGE_ROOT = None
REDMINE_ORIGIN_URL = None
PAGES = None
FAIL_FAST = False

# === Local wiki export location ===
wiki_dir = None

# === Connect to Confluence ===
confluence = None
auth = None

# === JSON Request Headers ===
JSON_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json"
}

def textile_to_html(textile_text):
    """
    Convert textile using pandoc or python-textile
    If the number of tables in the two does not match, go thru pandoc
    version and replace unconverted tables with their equivalent from python-textile.
    Otherwise, just return the pandoc version.
    """
    proc = subprocess.run(
        ['pandoc', '--from=textile', '--to=html5', '--wrap=none'],
        input=textile_text.encode('utf-8'),
        stdout=subprocess.PIPE
    )
    presult = proc.stdout.decode('utf-8')
    psoup = BeautifulSoup(presult, 'html.parser')

    return presult # Skip textile fallback for now

    tresult = textile.textile(textile_text)
    tsoup = BeautifulSoup(tresult, 'html.parser')

    with open('pandoc_debug_output.html', 'w', encoding='utf-8') as fdebug:
        fdebug.write(presult)
    with open('textile_debug_output.html', 'w', encoding='utf-8') as fdebug:
        fdebug.write(tresult)

    if len(psoup.find_all('table')) != len(tsoup.find_all('table')):
        print("   ⚠️  Mismatch in table conversion between pandoc and python-textile. Merging results...")
        retval = u''
        for line in presult.split('\n'):
            if line.startswith('<p>|'):
                retval += textile.textile(line[3:-4].replace('<br />', '\n').replace('<br/>', '\n'))
            else:
                retval += line
        presult = retval
        psoup = BeautifulSoup(presult, 'html.parser')

    return presult

def html_replace_img_with_confluence_macro(html, attachments):
    # Only replace images that match an attachment
    filenames = {os.path.basename(f) for f in attachments}
    def replacer(match):
        fname = match.group(1)
        if fname in filenames:
            return f'<ac:image><ri:attachment ri:filename="{fname}"/><ac:alt>{fname}</ac:alt></ac:image>'
        else:
            return match.group(0)
    return re.sub(r'<img[^>]+src=[\'"]([^\'"]+)[\'"][^>]*>', replacer, html)

def html_convert_links(html):
    """
    Make links clicable, convert links from redmine style to confluence style
    If link points to original redmine, and is a wiki (/projects/../wiki/...) link,
    convert it to a confluence page link.
    If link points to original redmine, and is an issue (/issues/...) link,
    convert it to jira issue link / search (by RedmineID).
    """
    soup = BeautifulSoup(html, 'html.parser')
    for a in soup.find_all('a', href=True):
        # If a-href is under a <code>, <pre> or <notextile>, ignore it..
        parent_tags = [parent.name for parent in a.parents]
        if 'code' in parent_tags or 'pre' in parent_tags or 'notextile' in parent_tags:
            continue

        href = a['href']
        if REDMINE_ORIGIN_URL and href.startswith(REDMINE_ORIGIN_URL):
            rel_link = href.replace(REDMINE_ORIGIN_URL, '')
            if rel_link.startswith('/projects/') and '/wiki/' in rel_link:
                # Replace '<a href="...">...</a>' with confluence <ac:link> macro
                page_name = rel_link.split('/wiki/')[-1].strip().rstrip(':')
                #page_name = page_name.replace(' ', '+').replace('_', '+')
                link_name = page_name.replace('[', '(').replace(']', ')')
                html = (
                    f'<ac:link>'
                        f'<ri:page ri:content-title="{page_name} (Legacy)"/>'
                        f'<ac:plain-text-link-body><![CDATA[{link_name}]]></ac:plain-text-link-body>'
                    f'</ac:link>'
                )
                a.replace_with(BeautifulSoup(html, 'html.parser'))
            elif '/issues/' in rel_link:
                # Issue link
                issue_id = rel_link.split('/issues/')[-1].split('/')[0]
                jira_search_link = f"{CONFLUENCE_URL}/issues/?jql=\"RedmineID:{issue_id}\""
                a['href'] = jira_search_link

    # Find http links and make sure they are clickable
    url_pattern = re.compile(
        #r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
        r'https?://[^\s<>"\')]+'
    )
    for url in soup.find_all(string=url_pattern):
        new_content = url
        for match in url_pattern.finditer(url):
            link = match.group(0)
            link_html = f'<a href="{link}">{link}</a>'
            new_content = new_content.replace(link, link_html)
        new_soup = BeautifulSoup(new_content, 'html.parser')
        url.replace_with(new_soup)

    # Find text like '[[Page Name]]' and '[[Page Name|Link Text]]' and convert to confluence links
    pattern = re.compile(r'\[\[([^\|\]]+)(\|([^\]]+))?\]\]')
    for text_node in soup.find_all(string=pattern):
        new_content = text_node
        for match in pattern.finditer(text_node):
            page_name = match.group(1).strip().rstrip(':')
            link_text = match.group(3).strip() if match.group(3) else page_name
            link_text = link_text.replace('[', '(').replace(']', ')')
            confluence_link = (
                f'<ac:link>'
                    f'<ri:page ri:content-title="{page_name} (Legacy)"/>'
                    f'<ac:plain-text-link-body><![CDATA[{link_text}]]></ac:plain-text-link-body>'
                f'</ac:link>'
            )
            new_content = new_content.replace(match.group(0), confluence_link)
        new_soup = BeautifulSoup(new_content, 'html.parser')
        text_node.replace_with(new_soup)
           

    result = str(soup)

    # Fix CDATA newline issues (introduced by BeautifulSoup)
    result = re.sub(r'<!\[CDATA\[(.*?)\]\]>', lambda m: '<![CDATA[' + m.group(1).replace('\n', ' ') + ']]>', result, flags=re.S)

    return result


# Matches an escaped inner <code class="bash"> ... </code> that lives inside <pre><code>...</code></pre>
ESCAPED_INNER_CODE_RE = re.compile(
    r'^\s*<code\b([^>]*)>(.*?)</code>\s*$',
    re.IGNORECASE | re.DOTALL
)

CLASS_ATTR_RE = re.compile(r'class\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)

LANG_MAP = {
    "bash": "sh",
    "shell": "sh",
    "zsh": "sh",
    "console": "sh",
    "c#": "csharp",
    "cs": "csharp",
}

def _safe_cdata(text: str) -> str:
    # CDATA cannot contain the literal "]]>" sequence
    return text.replace("]]>", "]]]]><![CDATA[>")

def _pick_lang_from_class_attr(class_value: str | None) -> str | None:
    """
    Your style: class="bash" or class="python otherclass".
    We pick the first token as the language.
    """
    if not class_value:
        return None
    tokens = class_value.strip().split()
    lang = tokens[0].lower() if tokens else None
    return LANG_MAP.get(lang, lang) if lang else None

def convert_code_blocks(html: str) -> str:
    """
    Convert Pandoc-style code blocks:
      <pre><code>&lt;code class=&quot;bash&quot;&gt;
      echo hi
      &lt;/code&gt;</code></pre>
    into Confluence code macro (storage).
    """
    soup = BeautifulSoup(html, "html.parser")

    for pre in soup.find_all("pre"):
        outer_code = pre.find("code")

        # If no <code>, treat <pre> as raw code
        if outer_code is None:
            continue

        # Decode the *escaped* inner <code ...>...</code>
        raw = outer_code.decode_contents()
        unescaped = html_stdlib.unescape(raw)

        m = ESCAPED_INNER_CODE_RE.match(unescaped)
        if m:
            attrs = m.group(1) or ""
            inner = m.group(2) or ""

            cm = CLASS_ATTR_RE.search(attrs)
            lang = _pick_lang_from_class_attr(cm.group(1) if cm else None)

            # Inner may still contain entities; unescape again
            code_text = html_stdlib.unescape(inner)
        else:
            # Normal case: <pre><code class="bash">...</code></pre>
            lang = _pick_lang_from_class_attr(outer_code.get("class")[0] if outer_code.get("class") else None)
            code_text = outer_code.get_text("\n")

        # Normalize line endings and keep content as-is
        code_text = (code_text or "").replace("\r\n", "\n").replace("\r", "\n")
        code_text = _safe_cdata(code_text)

        # Build Confluence code macro
        macro = soup.new_tag("ac:structured-macro")
        macro["ac:name"] = "code"

        if lang:
            p_lang = soup.new_tag("ac:parameter")
            p_lang["ac:name"] = "language"
            p_lang.string = lang
            macro.append(p_lang)

        p_theme = soup.new_tag("ac:parameter")
        p_theme["ac:name"] = "theme"
        p_theme.string = "Default"
        macro.append(p_theme)

        body = soup.new_tag("ac:plain-text-body")
        body.string = CData(code_text)
        macro.append(body)

        pre.replace_with(macro)

    return soup.encode(formatter=None).decode("utf-8")

def create_page_hierarchy(wiki_dir):
    hierarchy = {}
    for fname in os.listdir(wiki_dir):
        if not fname.endswith('.txt'):
            continue

        title = fname[:-4]

        if PAGES and title not in PAGES:
            print(f"❗ Skipping page '{title}' as not in specified pages list.")
            continue

        path = os.path.join(wiki_dir, fname)
        parent = None
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith("Parent Page:"):
                    parent = line.replace("Parent Page:", '').strip()
                    if parent == 'None' and title == CONFLUENCE_PAGE_ROOT:
                        parent = None
                    elif parent == 'None':
                        parent = CONFLUENCE_PAGE_ROOT
                    break
        attachments = []
        attach_dir = os.path.join(wiki_dir, f"{title}_attachments")
        if os.path.exists(attach_dir):
            attachments = [os.path.join(attach_dir, af) for af in os.listdir(attach_dir)]
        images = []
        img_dir = os.path.join(wiki_dir, f"{title}_images")
        if os.path.exists(img_dir):
            images = [os.path.join(img_dir, af) for af in os.listdir(img_dir)]
        hierarchy[title] = {
            'file': path,
            'parent': parent,
            'attachments': attachments,
            'images': images
        }


    print(f"🏗️  Constructed page hierarchy with {len(hierarchy)} pages.")
    #pprint(hierarchy)
    #raise "fail"
    return hierarchy

def get_page_id(title, space, parent_id=None):
    results = confluence.get_page_id(space, title)
    if results:
        return results
    return None

def upload_attachments_to_page(page_id, file_paths):
    url = f"/rest/api/content/{page_id}/child/attachment"
    for file_path in file_paths:
        filename = os.path.basename(file_path)
        filesize = os.path.getsize(file_path)
        print(f"   ⏳ Uploading {filename} ({filesize} bytes) to page {page_id}")
        if filesize == 0:
            print(f"   ⚠️  Skipping empty file: {filename}")
            continue
        with open(file_path, 'rb') as fobj:
            files = {'file': (filename, fobj)}
            try:
                resp = confluence.request(
                    method='POST',
                    path=url,
                    files=files,
                    headers={'X-Atlassian-Token': 'nocheck'}
                )
                # Check response for status or text
                if hasattr(resp, 'status_code'):
                    status = resp.status_code
                    text = resp.text
                else:
                    status = resp.get('statusCode', 'n/a')
                    text = str(resp)
                if status in (200, 201):
                    print(f"   📎 Uploaded {filename} to Confluence page")
                else:
                    print(f"   ❌ Failed to upload {filename}: HTTP {status}\n{text}")
            except Exception as e:
                err = str(e)
                if "existing attachment" in err:
                    print(f"   ⚠️  Attachment {filename} already exists on page. Skipping upload..")
                    continue
                print(f"   ❌ Exception uploading {filename}: {e}")
                continue


def create_page(
    spaceid,
    title,
    body,
    parent_id=None,
    type="page",
    representation="storage",
    status="current",
):
    """
    Create page from scratch
    :param space:
    :param title:
    :param body:
    :param parent_id:
    :param representation: OPTIONAL: either Confluence 'storage' or 'wiki' markup format
    :param status: either 'current' or 'draft'
    :return:
    """
    url = f"{CONFLUENCE_URL}/wiki/api/v2/pages"
    data = {
        "spaceId": spaceid,
        "status": status,
        "title": title,
        "body": {
            "representation": representation,
            "value": body,
        },
    }
    if parent_id:
        data["parentId"] = parent_id

    try:
        response = requests.post(url, auth=auth, headers=JSON_HEADERS, json=data)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            raise ApiPermissionError(
                "The calling user does not have permission to view the content",
                reason=e,
            )

        raise

    if response.status_code not in (200, 201):
        raise Exception(f"Failed to create page '{title}': HTTP {response.status_code}\n{response.text}")


    # Set page as full width
    page_id = response.json().get('id')
    url_fullwidth = f"{CONFLUENCE_URL}/wiki/api/v2/pages/{page_id}/properties"
    data = {
        "key": "content-appearance-published",
        "value": "full-width"
    }
    response_fullwidth = requests.post(url_fullwidth, auth=auth, headers=JSON_HEADERS, json=data)

    if response_fullwidth.status_code not in (200, 201):
        print(f"   ⚠️ Failed to set page '{title}' as full width: HTTP {response_fullwidth.status_code}\n{response_fullwidth.text}")
        raise Exception(f"Failed to set page '{title}' as full width: HTTP {response_fullwidth.status_code}\n{response_fullwidth.text}")

    return response.json()


def create_confluence_wiki(wiki_dir):
    hierarchy = create_page_hierarchy(wiki_dir)
    created_pages = {}

    print(f"⚙️  Starting import of {len(hierarchy)} pages into Confluence space '{CONFLUENCE_SPACE_KEY}'")

    # First, create all root pages (no parent)
    for page, info in hierarchy.items():
        title = f"{page} (Legacy)"

        if info['parent'] is None:
            print(f"➡️  Creating root page '{title}'...")

            with open(info['file'], 'r', encoding='utf-8') as f:
                raw_content = f.read()
            split = raw_content.find('---\n\n')
            if split != -1:
                body = raw_content[split+5:]
            else:
                body = raw_content
            html_body = textile_to_html(body)
            html_body = html_replace_img_with_confluence_macro(html_body, info['attachments'] + info['images'])
            html_body = html_convert_links(html_body)
            html_body = convert_code_blocks(html_body)

            with open(info['file'][:-4] + '.html', 'w', encoding='utf-8') as fhtml:
                fhtml.write(html_body)
            try:
            # print status, including emoji
                result = create_page(
                    spaceid=CONFLUENCE_SPACE_ID,
                    title=title,
                    body=html_body,
                    parent_id=CONFLUENCE_PARENT_FOLDER,
                    representation='storage'
                )
                page_id = created_pages[page] = result['id']
                print(f"   ✅ Created page '{title}' with ID {result['id']}")
                upload_attachments_to_page(page_id, info['attachments'] + info['images'])
            except Exception as e:
                error_str = str(e)
                if "already exists" in error_str:
                    print(f"⚠️ Page '{title}' already exists. Skipping creation. ({e})")
                    page_id = confluence.get_page_id(CONFLUENCE_SPACE_KEY, title)
                    if not page_id:
                        print(f"    ⚠️ Could not find existing page ID for '{title}', skipping attachments.")
                        continue
                    else:
                        created_pages[page] = page_id
                        upload_attachments_to_page(page_id, info['attachments'] + info['images'])
                    continue
                else:
                    print(f"⚠️ Exception while creating page '{title}': {e}")
                    if FAIL_FAST:
                        raise
                    continue

    # Then, create all child pages
    pages_remaining = {k: v for k, v in hierarchy.items() if v['parent'] is not None}
    progress = True
    while pages_remaining and progress:
        progress = False
        for page, info in list(pages_remaining.items()):
            title = f"{page} (Legacy)"
            parent = info['parent']
            if parent in created_pages:
                print(f"➡️  Creating child page '{title}' under parent '{parent} (Legacy)'...")

                with open(info['file'], 'r', encoding='utf-8') as f:
                    raw_content = f.read()
                split = raw_content.find('---\n\n')
                if split != -1:
                    body = raw_content[split+5:]
                else:
                    body = raw_content
                html_body = textile_to_html(body)
                html_body = html_replace_img_with_confluence_macro(html_body, info['attachments'] + info['images'])
                html_body = html_convert_links(html_body)
                html_body = convert_code_blocks(html_body)

                with open(info['file'][:-4] + '.html', 'w', encoding='utf-8') as fhtml:
                    fhtml.write(html_body)

                try:
                    print(f"➡️  Creating root page '{title}'...")
                    result = create_page(
                        spaceid=CONFLUENCE_SPACE_ID,
                        title=title,
                        body=html_body,
                        parent_id=created_pages[parent],
                        representation='storage'
                    )
                    print(f"   ✅ Created page '{title}' with ID {result['id']}")
                    page_id = created_pages[page] = result['id']
                    upload_attachments_to_page(page_id, info['attachments'] + info['images'])
                    del pages_remaining[page]
                    progress = True
                except Exception as e:
                    error_str = str(e)
                    if "already exists" in error_str:
                        print(f"⚠️ Page '{title}' already exists. Skipping creation.")
                        page_id = confluence.get_page_id(CONFLUENCE_SPACE_KEY, title)
                        if not page_id:
                            print(f"    ⚠️ Could not find existing page ID for '{title}', skipping attachments.")
                            continue
                        else:
                            created_pages[page] = page_id
                            upload_attachments_to_page(page_id, info['attachments'] + info['images'])
                            del pages_remaining[page]
                            progress = True
                        continue
                    else:
                        print(f"⚠️ Exception while creating page '{title}': {e}")
                        if FAIL_FAST:
                            raise
                        continue

    if pages_remaining:
        print("⚠️ These pages could not be placed due to missing parent(s):")
        for k in pages_remaining:
            print(f"  - {k}")


def get_spaceid_by_key(space_key):
    """
    Get Confluence space ID by its key.
    Uses GET /wiki/api/v2/space endpoint to search for the space by key.
    """
    url = f"{CONFLUENCE_URL}/wiki/api/v2/spaces"
    params = {
        'keys': space_key
    }
    response = requests.get(url, params=params, auth=auth, headers=JSON_HEADERS)
    if response.status_code != 200:
        raise Exception(f"Failed to get space ID for '{space_key}': HTTP {response.status_code}\n{response.text}")
    data = response.json()
    results = data.get('results', [])
    for item in results:
        if item.get('key') == space_key:
            return item.get('id')
    raise Exception(f"Space '{space_key}' not found in Confluence.")

def get_homepageid_by_key(space_key):
    """
    Get Confluence space homepage ID by its key.
    Uses GET /wiki/api/v2/space endpoint to search for the space by key.
    """
    url = f"{CONFLUENCE_URL}/wiki/api/v2/spaces"
    params = {
        'keys': space_key
    }
    response = requests.get(url, params=params, auth=auth, headers=JSON_HEADERS)
    if response.status_code != 200:
        raise Exception(f"Failed to get homepage ID for '{space_key}': HTTP {response.status_code}\n{response.text}")
    data = response.json()
    results = data.get('results', [])
    for item in results:
        if item.get('key') == space_key and 'homepageId' in item:
            return item.get('homepageId')
    raise Exception(f"Space '{space_key}' not found, or has no homepage-id.")

def get_folder_id_by_name(folder_name):
    homeid = get_homepageid_by_key(CONFLUENCE_SPACE_KEY)
    url = f"{CONFLUENCE_URL}/wiki/api/v2/pages/{homeid}/descendants?depth=1"
    response = requests.get(url, auth=auth, headers=JSON_HEADERS)
    if response.status_code != 200:
        raise Exception(f"Failed to get folder ID for '{folder_name}': HTTP {response.status_code}\n{response.text}")
    data = response.json()
    results = data.get('results', [])
    for item in results:
        if item.get('title') == folder_name:
            return item.get('id')
    raise Exception(f"Folder '{folder_name}' not found in Confluence space '{CONFLUENCE_SPACE_KEY}'.")


# Parse arguments:
#  --input => path to folder with Redmine issues (default: 'redmine-issues')
#  --confluence-url => Confluence instance URL
#  --confluence-user => Confluence user email
#  --confluence-token => Confluence API token
#  --confluence-space => Confluence space key
#  --confluence-folder => Confluence parent forlder name
#  --confluence-page-root => Confluence base page title (the one mapped to Wiki on redmine)
#  --origin-url => Redmine instance URL
#  --overwrite => overwrite (delete+create) existing issues
#  --fail-fast => stop on first error
#  --pages => comma-separated list of page titles to import (default: all)
#  --help => show this help
def parse_args():
    import argparse
    parser = argparse.ArgumentParser(description='Import local wiki export into Confluence.')
    parser.add_argument('--input', type=str, default=wiki_dir, help='Path to folder with local wiki export')
    parser.add_argument('--confluence-url', type=str, default=CONFLUENCE_URL, help='Confluence instance URL')
    parser.add_argument('--confluence-user', type=str, default=CONFLUENCE_USER, help='Confluence user email')
    parser.add_argument('--confluence-token', type=str, default=CONFLUENCE_API_TOKEN, help='Confluence API token')
    parser.add_argument('--confluence-space', type=str, default=CONFLUENCE_SPACE_KEY, help='Confluence space key')
    parser.add_argument('--confluence-folder', type=str, help='Confluence parent folder name')
    parser.add_argument('--confluence-page-root', type=str, help='Confluence base page title (for mapping Wiki root)')
    parser.add_argument('--origin-url', type=str, help='Original redmine instance URL (for link conversion)')
    parser.add_argument('--overwrite', action='store_true', help='Overwrite existing pages')
    parser.add_argument('--fail-fast', action='store_true', help='Stop on first error')
    parser.add_argument('--pages', type=str, help='Comma-separated list of page titles to import (default: all)')
    args = parser.parse_args()
    return args


def main():
    global wiki_dir, confluence, auth
    global CONFLUENCE_URL, CONFLUENCE_USER, CONFLUENCE_API_TOKEN, CONFLUENCE_PARENT_FOLDER
    global CONFLUENCE_SPACE_ID, CONFLUENCE_SPACE_KEY, CONFLUENCE_PAGE_ROOT
    global PAGES, FAIL_FAST, REDMINE_ORIGIN_URL
    args = parse_args()

    wiki_dir = args.input
    CONFLUENCE_URL = args.confluence_url
    CONFLUENCE_USER = args.confluence_user
    CONFLUENCE_API_TOKEN = args.confluence_token
    CONFLUENCE_SPACE_KEY = args.confluence_space
    CONFLUENCE_SPACE_ID = get_spaceid_by_key(CONFLUENCE_SPACE_KEY)
    CONFLUENCE_PAGE_ROOT = args.confluence_page_root
    REDMINE_ORIGIN_URL = args.origin_url
    FAIL_FAST = args.fail_fast

    auth = (CONFLUENCE_USER, CONFLUENCE_API_TOKEN)

    if args.confluence_folder:
        CONFLUENCE_PARENT_FOLDER = get_folder_id_by_name(args.confluence_folder)

    if args.pages:
        PAGES = [p.strip() for p in args.pages.split(',')]
        print(f"Importing only specified pages: {PAGES}")

    confluence = Confluence(
        url=CONFLUENCE_URL,
        username=CONFLUENCE_USER,
        password=CONFLUENCE_API_TOKEN,
        cloud=True
    )

    create_confluence_wiki(wiki_dir)


if __name__ == "__main__":
    main()
