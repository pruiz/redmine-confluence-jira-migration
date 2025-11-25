import requests
import os
import json
import time

# === Configuration ===
project_id = 'verifid'
api_key = 'd3e6a695b0e5b68539ef19e0801a951a7e338efe'
base_url = 'https://dev.crt0.net'
headers = {'X-Redmine-API-Key': api_key}

output_folder = 'redmine_issues'
os.makedirs(output_folder, exist_ok=True)

# === Change offset in case of download disruption to resume work ===
offset = 0
limit = 100
all_issues = []

print(f"\U0001F4E5 Starting to fetch issues from '{project_id}'")

# === Step 1: Paginate through all issues (including closed) ===
while True:
    url = f'{base_url}/issues.json?project_id={project_id}&status_id=*&offset={offset}&limit={limit}'
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print(f"❌ Failed to fetch issues: {response.status_code}")
        print(response.text)
        break

    data = response.json()
    issues = data.get('issues', [])
    if not issues:
        break

    print(f"🔹 Retrieved {len(issues)} issues (offset {offset})")

    for issue in issues:
        issue_id = issue['id']
        json_path = os.path.join(output_folder, f'issue_{issue_id}.json')
        txt_path = os.path.join(output_folder, f'issue_{issue_id}.txt')

        issue_url = f'{base_url}/issues/{issue_id}.json?include=journals,attachments,changesets'
        detail_resp = requests.get(issue_url, headers=headers)

        if detail_resp.status_code != 200:
            print(f"⚠️ Failed to get full data for issue #{issue_id}")
            continue

        full_data = detail_resp.json().get('issue', {})
        all_issues.append(full_data)

        # === Save JSON ===
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(full_data, f, indent=2)

        # === Save as readable text ===
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(f"Issue #{issue_id}\n")
            f.write(f"Subject: {full_data.get('subject')}\n")
            f.write(f"Status: {full_data.get('status', {}).get('name')}\n")
            f.write(f"Tracker: {full_data.get('tracker', {}).get('name')}\n")
            f.write(f"Priority: {full_data.get('priority', {}).get('name')}\n")
            f.write(f"Assigned to: {full_data.get('assigned_to', {}).get('name', 'Unassigned')}\n")
            f.write(f"Author: {full_data.get('author', {}).get('name')}\n")
            f.write(f"Created: {full_data.get('created_on')}\n")
            f.write(f"Updated: {full_data.get('updated_on')}\n")
            f.write(f"Description:\n{full_data.get('description', '')}\n\n")

            # Journals (comments)
            f.write("--- Comments ---\n")
            for journal in full_data.get('journals', []):
                user = journal.get('user', {}).get('name', 'Unknown')
                notes = journal.get('notes', '')
                created = journal.get('created_on')
                if notes:
                    f.write(f"\n[{created}] {user}:\n{notes}\n")

        # === Step 2: Download issue as PDF ===
#        pdf_url = f'{base_url}/issues/{issue_id}.pdf'
#        print(f"   📄 Downloading PDF for issue #{issue_id}")
#        pdf_resp = requests.get(pdf_url, headers=headers)
#        if pdf_resp.status_code == 200:
#            pdf_path = os.path.join(output_folder, f'issue_{issue_id}.pdf')
#            with open(pdf_path, 'wb') as f:
#                f.write(pdf_resp.content)
#        else:
#            print(f"   ⚠️ Failed to download PDF for issue #{issue_id}: {pdf_resp.status_code}")

        # === Step 3: Download attachments ===
        attachments = full_data.get('attachments', [])
        if attachments:
            attachment_dir = os.path.join(output_folder, f'issue_{issue_id}_attachments')
            os.makedirs(attachment_dir, exist_ok=True)

            for att in attachments:
                filename = att.get('filename')
                content_url = att.get('content_url')
                if not filename or not content_url:
                    continue

                print(f"   📎 Downloading attachment: {filename}")
                att_resp = requests.get(content_url, headers=headers)
                if att_resp.status_code == 200:
                    att_path = os.path.join(attachment_dir, filename)
                    with open(att_path, 'wb') as f:
                        f.write(att_resp.content)
                else:
                    print(f"   ⚠️ Failed to download attachment '{filename}': {att_resp.status_code}")

    offset += limit
    time.sleep(1.5)  # Polite delay to avoid hammering the server

print(f"\n✅ Completed. Total issues downloaded: {len(all_issues)}")
