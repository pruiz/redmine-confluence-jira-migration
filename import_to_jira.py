import os
import re
import json
import requests
import time
import subprocess
from pprint import pprint
from datetime import datetime

# === Jira configuration ===
JIRA_URL = None
JIRA_USER = None
JIRA_API_TOKEN = None
JIRA_PROJECT_KEY = None

# === Source Redmine issues (txt files for comments, description, and JSON of the issue)===
redmine_issues_folder = None

auth = None

# === Modify this mapping based on your mapping, "REDMINE" : "JIRA"===

issue_type_map = {
    "Bug" : "Bug",
    "Feature" : "New Feature",
    "Support" : "Support",
    "Task" : "Task",
    "Incident" : "Incident",
}

priority_map = {
    "Immediate" : "Immediate",
    "Urgent" : "Urgent",
    "High" : "High",
    "Normal" : "Normal",
    "Low" : "Low",
}

status_map = {
    "New" : "NEW",
    "Assigned" : "ASSIGNED",
    "Resolved" : "RESOLVED",
    "Feedback" : "WAITING FOR FEEDBACK",
    "Closed" : "CLOSED",
    "Rejected" : "REJECTED",
}

transitions_map = {
    "BACKLOG", "Move to Backlog",
    "REJECTED", "Reject Issue",
    "ASSIGNED", "Assign Developer",
    "IN PROGRESS", "Start Work",
    "IN REVIEW", "Submit to Review",
    "DEPLOYED", "Approve & Deploy",
    "RESOLVED", "Mark As Resolved",
    "CLOSED", "Close Resolved Issue",
}

transitions_fields = {
    "ASSIGNED": ["Assignee", "Start date"],
    "IN PROGRESS": [],
    "IN REVIEW": ["% Done", "Git Branch / Pull Request"],
    "DEPLOYED": [],
    "RESOLVED": ["Resolution"],
}

resolution_map = {
    # "N/A" : None --> Does not apply to jira
    "Done" : "Done",
    "Won't do" : "Won't Do",
    "Duplicate" : "Duplicate",
    "Works For Me" : "Works for me",
    "Invalid": "Invalid",
}

user_map = {
    # Map Redmine usernames or emails to Jira account emails if needed
}

user_ids = {
    # Map Redmine user IDs to Jira account IDs if needed
}

field_ids = {
    # Cache of Jira's custom field name to field ID mappings
}

SUMMARY_CHAR_LIMIT = 500    # Length of summary if content is too long, you can modify this to define how much of summary to keep in case you reach the max limit of ADF

def try_get_user_id_for(user):
    email = user_map.get(user, user)

    if email in user_ids:
        return user_ids[email]

    resp = requests.get(
        f"{JIRA_URL}/rest/api/3/user/search",
        auth=auth,
        params={"query": email}
    )
    if resp.status_code == 200:
        users = resp.json()
        if users and 'accountId' in users[0]:
            id = users[0]['accountId']
            print(f"👱 Found Jira user-id for {email}.. and added to cache with id ({id}).")
            user_ids[email] = id
            return id
        else:
            print(f"⚠️  No Jira user found for '{email}'")
    else:
        print(f"⚠️  Failed to search user by email '{email}': {resp.text}")
    return None

def try_get_field_id_for(field_name):
    if field_name in field_ids:
        return field_ids[field_name]

    resp = requests.get(
        f"{JIRA_URL}/rest/api/3/field",
        auth=auth,
        headers={"Accept": "application/json"}
    )
    if resp.status_code == 200:
        fields = resp.json()
        for field in fields:
            if field['name'] == field_name:
                id = field['id']
                print(f"🔍 Found Jira field-id for '{field_name}': {id} and added to cache.")
                field_ids[field_name] = id
                return id
        print(f"⚠️  No Jira field found for '{field_name}'")
    else:
        print(f"⚠️  Failed to get Jira fields: {resp.text}")
    return None

def get_field_id_for(field_name):
    id = try_get_field_id_for(field_name)
    if not id:
        raise Exception(f"Field '{field_name}' not found in Jira.")
    return id


def get_redmine_custom_field_value(redmine_issue, field_name):
    for field in redmine_issue.get("custom_fields", []):
        if field.get("name", None) == field_name:
            return field.get("value", None)
    return None

def preprocess_redmine_plaintext(text):
    text = re.sub(r'\[\[([^\]]+)\]\]', r'[\1]', text)
    text = re.sub(r'(^\d+\.\s+.+)', r'**\1**', text, flags=re.MULTILINE)
    text = re.sub(r'(\{[\s\S]*?\})', r'```\n\1\n```', text)
    text = re.sub(
        r'((?:[A-Z ]+, ?)+)', 
        lambda m: '\n'.join(f'- {w.strip()}' for w in m.group(1).split(',')),
        text
    )
    text = re.sub(r'(\r\n|\r|\n){2,}', '\n\n', text)
    return text

def adf_heading(text, level=3):
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [
            {"type": "text", "text": text}
        ]
    }

def adf_bold_paragraph(text):
    return {
        "type": "paragraph",
        "content": [
            {"type": "text", "text": text, "marks": [{"type": "strong"}]}
        ]
    }

def adf_infobox(text):
    return {
        "type": "panel",
        "attrs": {"panelType": "info"},
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": text}
                ]
            }
        ]
    }

def adf_paragraphs_from_markdown(md):
    paragraphs = [p.strip() for p in md.strip().split('\n\n') if p.strip()]
    return [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": p}]
        }
        for p in paragraphs
    ]   

def textile_to_markdown_with_pandoc(textile_text):
    proc = subprocess.run(
        ['pandoc', '--from=textile', '--to=markdown'],
        input=textile_text.encode('utf-8'),
        stdout=subprocess.PIPE
    )
    return proc.stdout.decode('utf-8')

def adf_metadata_table(redmine_issue):
    customs = ["Resolution", "CC Ticket", "Customer Ticket"]
    fields = [
        ("Redmine ID", redmine_issue.get("id", "")),
        ("Author", redmine_issue.get("author", {}).get("name", "")),
        ("Status", redmine_issue.get("status", {}).get("name", "")),
        ("Tracker", redmine_issue.get("tracker", {}).get("name", "")),
        ("Priority", redmine_issue.get("priority", {}).get("name", "")),
        ("Assigned To", redmine_issue.get("assigned_to", {}).get("name", "")),
        ("Created", redmine_issue.get("created_on", "")),
        ("Updated", redmine_issue.get("updated_on", "")),
        ("Release", redmine_issue.get("fixed_version", {}).get("name", None)),
    ]

    for custom in customs:
        value = get_redmine_custom_field_value(redmine_issue, custom)
        if value:
            fields.append((custom, value))

    rows = [
        [
            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": str(k)}]}]},
            {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": str(v)}]}]}
        ]
        for k, v in fields if v
    ]
    return {
        "type": "table",
        "content": [
            {
                "type": "tableRow",
                "content": [
                    {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Field"}]}]},
                    {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Value"}]}]},
                ]
            }
        ] + [
            {"type": "tableRow", "content": row}
            for row in rows
        ]
    }

def adf_changesets_table(redmine_issue):
    changesets = redmine_issue.get("changesets", [])
    if not changesets:
        return None

    print(f"🔍 Found {len(changesets)} changesets for Redmine #{redmine_issue.get('id')}")

    rows = []
    for cs in changesets:
        rows.append({
            "type": "tableRow", 
            "content": [
                {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "\u200B" + str(cs.get("committed_on", ""))}]}]},
                {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": str(cs.get("revision", ""))}]}]},
                {"type": "tableCell", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "\u200B" + str(cs.get("user", {}).get("name", ""))}]}]}
            ]
        })
        rows.append({
            "type": "tableRow",
            "content": [{
                "type": "tableCell", "attrs": {"colspan": 3},
                "content": [{
                    "type": "paragraph", "content": [{"type": "text", "text": str(cs.get("comments", ""))}]
                }]
            }]
        })

    return {
        "type": "table",
        "content": [
            {
                "type": "tableRow",
                "content": [
                    {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Date"}]}]},
                    {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Revision"}]}]},
                    {"type": "tableHeader", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Author"}]}]},
                ]
            }
        ] + 
        rows
    }

def try_attach_file_to_jira(issue_key, file_path):
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments",
            auth=auth,
            headers={"X-Atlassian-Token": "no-check"},
            files={"file": (filename, f)}
        )
        if resp.status_code in (200, 201):
            print(f"   📎 Uploaded fallback file: {filename}")
        else:
            print(f"   ⚠️ Failed to upload fallback file '{filename}': {resp.text}")

        return resp.status_code in (200, 201)

def attach_file_to_jira(issue_key, file_path):
    ''' Attempts to attach file 5 five times before giving up '''
    max_retries = 5
    for attempt in range(max_retries):
        success = try_attach_file_to_jira(issue_key, file_path)
        if success:
            return
        else:
            print(f"   🔄 Retrying to upload file '{os.path.basename(file_path)}' (Attempt {attempt + 2}/{max_retries})")
            time.sleep(0.5)

def try_get_transition_fields(obj, keys):
    return {(try_get_field_id_for(k) or k): obj.get(k, None) for k in keys if k in obj}

def transition_jira_issue_to(issue_key, status_jira, fields = {}):
    # Get available transitions
    resp = requests.get(
        f"{JIRA_URL}/rest/api/3/issue/{issue_key}/transitions",
        auth=auth
    )
    if resp.status_code != 200:
        print(f"\t❌ Failed to get transitions for {issue_key}: {resp.text}")
        return False

    #print(f"\t➡️ Transitions available for issue {issue_key} -> '{resp.json()}'")

    transition_id = None
    transitions = resp.json().get('transitions', [])
    for t in transitions:
        if t['to']['name'].upper() == status_jira:
            transition_id = t['id']
            break

    if not transition_id and status_jira == 'RESOLVED':
        # Special case: RESOLVED requires multiple transitions
        return (
            transition_jira_issue_to(issue_key, 'ASSIGNED', fields) and
            transition_jira_issue_to(issue_key, 'IN PROGRESS', fields) and
            transition_jira_issue_to(issue_key, 'IN REVIEW', fields) and
            transition_jira_issue_to(issue_key, 'DEPLOYED', fields) and
            transition_jira_issue_to(issue_key, 'RESOLVED', fields)
        )

    if not transition_id and status_jira == 'CLOSED':
        # Special case: CLOSED requires RESOLVED first
        return (
            transition_jira_issue_to(issue_key, 'RESOLVED', fields) and
            transition_jira_issue_to(issue_key, 'CLOSED', fields)
        )

    if not transition_id:
        print(f"\t⚠️  No transition found for status '{status_jira}' on issue {issue_key}")
        return False

    payload = {
        "transition": {"id": transition_id},
        "fields": try_get_transition_fields(fields, transitions_fields.get(status_jira, []))
    }

    # Perform the transition
    resp2 = requests.post(
        f"{JIRA_URL}/rest/api/3/issue/{issue_key}/transitions",
        auth=auth,
        json=payload
    )
    if resp2.status_code in (200, 204):
        print(f"\t✅ Transitioned issue {issue_key} to '{status_jira}'")
        return True
    else:
        print(f"\t❌ Failed to transition issue {issue_key} to {status_jira}: {payload} => {resp2.text}")
        return False

def create_jira_issue(redmine_issue):
    issue_key = None
    summary = redmine_issue.get('subject', 'No subject')
    description_textile = redmine_issue.get('description', '')
    description_markdown = ""
    if description_textile:
        preprocessed = preprocess_redmine_plaintext(description_textile)
        description_markdown = textile_to_markdown_with_pandoc(preprocessed)
    else:
        description_markdown = "No description."

    adf_content = []
    adf_content.append(adf_infobox("Migrated from evicertia's tracker.."))
    adf_content.append(adf_metadata_table(redmine_issue))    
    adf_content.extend(adf_paragraphs_from_markdown(description_markdown))

    if redmine_issue.get("changesets", None):
        adf_content.append(adf_heading("Changesets.."))
        adf_content.append(adf_changesets_table(redmine_issue))    

    # Always attach the .txt and comments.txt for each issue
    issue_id = redmine_issue.get('id')
    created_on_str = redmine_issue.get('created_on', None)
    created_on = datetime.strptime(created_on_str, "%Y-%m-%dT%H:%M:%SZ")
    updated_on_str = redmine_issue.get('updated_on', None)
    updated_on = datetime.strptime(updated_on_str, "%Y-%m-%dT%H:%M:%SZ") if updated_on_str else None
    txt_path = os.path.join(redmine_issues_folder, f"issue_{issue_id}.txt")
    comments_txt_path = os.path.join(redmine_issues_folder, f"issue_{issue_id}_comments.txt")

    # Prepare main Jira issue payload
    kind = redmine_issue.get('tracker', {}).get('name', 'Task')
    status = redmine_issue.get('status', {}).get('name', 'New')
    status_jira = status_map.get(status, "NEW")
    priority = redmine_issue.get('priority', {}).get('name', 'Medium')
    priority_jira = priority_map.get(priority, "Medium")
    reporter = redmine_issue.get('author', {}).get('name', None)
    reporter_id = try_get_user_id_for(reporter) if reporter else None
    assignee = redmine_issue.get('assigned_to', {}).get('name', None)
    assignee_id = try_get_user_id_for(assignee) if assignee else None
    redmineid_fieldid = get_field_id_for("Redmine ID")
    environment_fieldid = get_field_id_for("Environment")
    environment = get_redmine_custom_field_value(redmine_issue, "Environment")

    print(f"➡️ Creating Jira issue for Redmine #{issue_id}: {assignee} / {reporter} / Status: {status_jira} / Priority: {priority_jira}")

    # Map other fields as needed
    #   - Assignee (only for open issues) - done
    #   - Created/Updated dates [to reflect originals] - Not possible
    #   - Git branch / pull request links if applicable - done
    #   - Labels, Components, etc. - done
    #   - Environment - done
    payload = {
        "fields": {
            "project": {"key": JIRA_PROJECT_KEY},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": adf_content
            },
            "issuetype": {"name": issue_type_map.get(kind, "Task")},
            "priority": {"name": priority_jira},
            redmineid_fieldid: str(issue_id),
        }
    }

    # XXX: Reporter cannot be assiged on team-managed Jira projects
    if reporter_id:
        payload["fields"]["reporter"] = {"id": reporter_id}

    if assignee_id and status_jira not in ['REJECTED', 'CLOSED']:
        payload["fields"]["assignee"] = {"id": assignee_id}

    server = get_redmine_custom_field_value(redmine_issue, "Server")
    if server:
        server_fieldid = get_field_id_for("Server")
        payload["fields"][server_fieldid] = server
   
    gitbranch = get_redmine_custom_field_value(redmine_issue, "Git Branch")
    if gitbranch:
        gitbranch_fieldid = get_field_id_for("Git Branch / Pull Request")
        payload["fields"][gitbranch_fieldid] = gitbranch

    component = get_redmine_custom_field_value(redmine_issue, "Component")
    if component:
        component_fieldid = get_field_id_for("Components")
        payload["fields"][component_fieldid] = component

    external_id = get_redmine_custom_field_value(redmine_issue, "Customer Ticket")
    if external_id:
        external_id_fieldid = get_field_id_for("Customer Ticket / External Reference")
        payload["fields"][external_id_fieldid] = external_id

    external_ticket = get_redmine_custom_field_value(redmine_issue, "CC Ticket")
    if external_ticket:
        external_ticket_fieldid = get_field_id_for("External ID")
        payload["fields"][external_ticket_fieldid] = external_ticket

    if status_jira in ['REJECTED', 'RESOLVED', 'CLOSED']:
        donepct_fieldid = get_field_id_for("% Done")
        payload["fields"][donepct_fieldid] = 100
        # Disable QA Review, so we can trransition to correct state
        payload["fields"][get_field_id_for('QA Review')] = { "value": "No" }


    if kind == 'Bug' and not environment:
        print("⚠️  Bug issue without Environment, setting to 'Dev'")
        environment = " Dev "

    if environment:
        payload["fields"][environment_fieldid] = { "name": environment }

    resp = requests.post(
        f"{JIRA_URL}/rest/api/3/issue",
        auth=auth,
        headers={"Content-Type": "application/json"},
        json=payload
    )

    # Fallback: If description too long, only add metadata and summary
    if resp.status_code not in (200, 201) and "CONTENT_LIMIT_EXCEEDED" in resp.text:
        print(f"⚠️ Content limit exceeded, retrying with summary only for Redmine #{issue_id}")
        summary_short = description_markdown[:SUMMARY_CHAR_LIMIT] + ("..." if len(description_markdown) > SUMMARY_CHAR_LIMIT else "")
        adf_content_fallback = []
        adf_content_fallback.append(adf_infobox("Migrated From evicertia's tracker.."))
        adf_content_fallback.append(adf_metadata_table(redmine_issue))
        adf_content_fallback.extend(adf_paragraphs_from_markdown(summary_short))
        payload["fields"]["description"]["content"] = adf_content_fallback
        resp2 = requests.post(
            f"{JIRA_URL}/rest/api/3/issue",
            auth=auth,
            headers={"Content-Type": "application/json"},
            json=payload
        )
        if resp2.status_code in (200, 201):
            issue_key = resp2.json()["key"]
            print(f"✅ Created Jira issue (summary only): {issue_key} for Redmine #{issue_id}")
            # Attach both .txt and comments.txt as files
            if os.path.exists(txt_path):
                attach_file_to_jira(issue_key, txt_path)
            if os.path.exists(comments_txt_path):
                attach_file_to_jira(issue_key, comments_txt_path)
            return issue_key
        else:
            print(f"❌ Failed to create Jira issue (summary fallback) for Redmine #{issue_id}: {resp2.text}")
            return None
    elif resp.status_code in (200, 201):
        issue_key = resp.json()["key"]
        print(f"✅ Created Jira issue: {issue_key} for Redmine #{issue_id}")
        # Attach both .txt and comments.txt as files
        if os.path.exists(txt_path):
            attach_file_to_jira(issue_key, txt_path)
        if os.path.exists(comments_txt_path):
            attach_file_to_jira(issue_key, comments_txt_path)
    else:
        print(f"❌ Failed to create Jira issue for Redmine #{issue_id}: {resp.text}")
        #print(f"❌ Failed to create Jira issue for Redmine #{issue_id}: {resp.text}\n- request:\n{json.dumps(payload, indent=2)}")
        return None

    # Now let's update status..
    if status_jira in ['REJECTED', 'RESOLVED', 'CLOSED']:
        fields = {
            'Assignee': { "id": (assignee_id or assignee_id or try_get_user_id_for(JIRA_USER)) },
            'Start date': (updated_on if updated_on else created_on).strftime("%Y-%m-%d"),
            '% Done': 100,
            'Git Branch / Pull Request': (gitbranch or '*missing*'),
            'Resolution' : { "name": resolution_map.get(get_redmine_custom_field_value(redmine_issue, "Resolution"), None) },
        }
        if not transition_jira_issue_to(issue_key, status_jira, fields):
            raise Exception("Failed to transition issue")
    elif redmine_issue.get('fixed_version', {}).get('name', None) == 'Backlog':
        if not transition_jira_issue_to(issue_key, 'BACKLOG'):
            raise Exception("Failed to transition issue")

    return issue_key


def upload_attachments_to_jira(issue_key, attachment_folder):
    if not os.path.exists(attachment_folder):
        return
    files = [os.path.join(attachment_folder, f) for f in os.listdir(attachment_folder)]
    for file_path in files:
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"{JIRA_URL}/rest/api/3/issue/{issue_key}/attachments",
                auth=auth,
                headers={"X-Atlassian-Token": "no-check"},
                files={"file": (filename, f)}
            )
            if resp.status_code in (200, 201):
                print(f"   📎 Uploaded attachment: {filename}")
            else:
                print(f"   ⚠️  Failed to upload attachment '{filename}': {resp.text}")

# Check if a Jira issue already exists for the given Redmine issue
# Uses /rest/api/3/search/jql as /rest/api/3/search has been deprecated
def check_jira_issue_exists(redmine_issue):
    redmineid_fieldid = get_field_id_for("Redmine ID")
    issue_id = redmine_issue.get('id')
    jql = f'"{redmineid_fieldid}" ~ "{issue_id}"'
    resp = requests.post(
        f"{JIRA_URL}/rest/api/3/search/approximate-count",
        auth=auth,
        headers={"Content-Type": "application/json"},
        json={"jql": jql}
    )
    if resp.status_code == 200:
        issues = resp.json().get('count', 0)
        return issues > 0
    else:
        print(f"⚠️  Failed to search Jira issues for Redmine ID '{issue_id}': {resp.text}")
        return False

def maybe_get_jiraids_for(redmine_issue):
    redmineid_fieldid = get_field_id_for("Redmine ID")
    issue_id = redmine_issue.get('id')
    jql = f'"{redmineid_fieldid}" ~ "{issue_id}"'
    resp = requests.post(
        f"{JIRA_URL}/rest/api/3/search/jql",
        auth=auth,
        headers={"Content-Type": "application/json"},
        json={"jql": jql}
    )
    print(f"🔍 Searching for Jira issues with Redmine ID '{issue_id}' -> {resp.json()}")
    if resp.status_code == 200:
        issues = resp.json().get('issues', [])
        if issues:
            return [issue['id'] for issue in issues]
    return None

def delete_jira_issue(issue_key):
    resp = requests.delete(
        f"{JIRA_URL}/rest/api/3/issue/{issue_key}",
        auth=auth
    )
    if resp.status_code == 204:
        print(f"🗑️  Deleted existing Jira issue {issue_key}")
        return

    print(f"⚠️  Failed to delete Jira issue {issue_key}: {resp.status_code} -> {resp.text}")
    raise Exception(f"Failed to delete Jira issue {resp.status_code} -> {issue_key}")

# Parse arguments:
#  --input => path to folder with Redmine issues (default: 'redmine-issues')
#  --jira-url => Jira instance URL
#  --jira-user => Jira user email
#  --jira-token => Jira API token
#  --jira-project => Jira project key
#  --emails => path to CSV file with user mappings (redmine_user, jira_email)
#  --errorlog => path to log file for errors
#  --skip-closed => skip closed issues
#  --issue-id => only import specific issue ID
#  --overwrite => overwrite (delete+create) existing issues
#  --fail-fast => stop on first error
#  --help => show this help
def parse_args():
    import argparse
    parser = argparse.ArgumentParser(description="Import Redmine issues to Jira.")
    parser.add_argument("--input", type=str, default="redmine-issues", help="Path to folder with Redmine issues")
    parser.add_argument("--jira-url", type=str, required=True, help="Jira instance URL")
    parser.add_argument("--jira-user", type=str, required=True, help="Jira user email")
    parser.add_argument("--jira-token", type=str, required=True, help="Jira API token")
    parser.add_argument("--jira-project", type=str, required=True, help="Jira project key")
    parser.add_argument("--emails", type=str, help="Path to CSV file with user mappings (redmine_user,jira_email)")
    parser.add_argument("--errorlog", type=str, help="Path to log file for errors")
    parser.add_argument("--skip-closed", action="store_true", help="Skip closed issues")
    parser.add_argument("--issue-id", type=int, help="Only import specific issue ID")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite (delete+create) existing issues")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first error")
    return parser.parse_args()

def readfile(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read_text().strip()

def main():
    global JIRA_URL, JIRA_USER, JIRA_API_TOKEN, JIRA_PROJECT_KEY, redmine_issues_folder, auth
    args = parse_args()

    JIRA_URL = args.jira_url
    JIRA_USER = args.jira_user
    JIRA_API_TOKEN = readfile(args.jira_token.lstrip('@')) if args.jira_token.startswith('@') else args.jira_token
    JIRA_PROJECT_KEY = args.jira_project
    redmine_issues_folder = args.input
    auth = (JIRA_USER, JIRA_API_TOKEN)
    # read user mappings from 'emails.csv', if present on script's folder
    if args.emails:
        emails_csv_path = os.path.join(os.path.dirname(__file__), args.emails)
        if not os.path.exists(emails_csv_path):
            print(f"⚠️  Emails mapping file '{emails_csv_path}' not found.")
            return

        with open(emails_csv_path, "r", encoding="utf-8") as f:
            global user_map
            for line in f:
                parts = line.strip().split(",")
                if len(parts) == 2:
                    user, email = parts
                    user_map[user.strip()] = email.strip()
            print(f"🔍 Loaded {len(user_map)} user mappings from 'emails.csv'.")
     
    errfile =  open(args.errorlog, "a", encoding="utf-8") if args.errorlog else None

    for fname in os.listdir(redmine_issues_folder):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(redmine_issues_folder, fname), "r", encoding="utf-8") as f:
            redmine_issue = json.load(f)

        if args.issue_id and redmine_issue.get('id') != args.issue_id:
            continue

        if args.skip_closed:
            status = redmine_issue.get('status', {}).get('name', 'New')
            if status in ['Closed', 'Rejected']:
                print(f"⏭️  Skipping closed issue Redmine #{redmine_issue.get('id')} with status '{status}'")
                continue

        if args.overwrite:
            ids = maybe_get_jiraids_for(redmine_issue)
            for id in ids or []:
                delete_jira_issue(id)
        elif check_jira_issue_exists(redmine_issue): 
            print(f"⏭️  Jira issue already exists for Redmine #{redmine_issue.get('id')}, skipping.")
            continue

        issue_key = create_jira_issue(redmine_issue)
        if issue_key:
            attachment_dir = os.path.join(
                redmine_issues_folder, 
                f"issue_{redmine_issue['id']}_attachments"
            )
            upload_attachments_to_jira(issue_key, attachment_dir)
        elif args.fail_fast:
            print(f"❌ Failing fast due to error on Redmine #{redmine_issue.get('id')}")
            raise Exception(f"Failed to import Redmine issue #{redmine_issue.get('id')}")
        elif errfile:
            errfile.write(f"Failed to import Redmine issue #{redmine_issue.get('id')}\n")
        time.sleep(0.6)  # Polite delay

if __name__ == "__main__":
    main()
