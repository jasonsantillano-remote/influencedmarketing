"""
Salesforce Influenced Marketing Report
Runs monthly (or on demand) and posts results to Slack.
"""

import os
import json
import urllib.request
from datetime import datetime, timedelta
from simple_salesforce import Salesforce


def connect_to_salesforce():
    return Salesforce(
        username=os.environ["SF_USERNAME"],
        password=os.environ["SF_PASSWORD"],
        security_token=os.environ["SF_SECURITY_TOKEN"],
        domain="remote-com"
    )


def get_recent_campaign_members(sf):
    """Get companies that attended campaigns in the last 30 days."""
    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    result = sf.query_all(f"""
        SELECT
            Contact.Account.Name,
            Contact.Account.Id,
            Campaign.Name,
            Campaign.StartDate,
            Contact.Name,
            Status
        FROM CampaignMember
        WHERE CreatedDate >= {thirty_days_ago}
          AND ContactId != null
          AND Contact.AccountId != null
        ORDER BY Campaign.StartDate DESC
    """)
    return result["records"]


def get_revenue_for_accounts(sf, account_ids, lookback_date):
    """Check for Closed Won opportunities on those accounts since the campaign."""
    if not account_ids:
        return {}

    ids_in = ", ".join(f"'{aid}'" for aid in account_ids)
    result = sf.query_all(f"""
        SELECT AccountId, Account.Name, Name, Amount, CloseDate
        FROM Opportunity
        WHERE AccountId IN ({ids_in})
          AND StageName = 'Closed Won'
          AND CloseDate >= {lookback_date}
        ORDER BY CloseDate DESC
    """)

    revenue_by_account = {}
    for opp in result["records"]:
        aid = opp["AccountId"]
        if aid not in revenue_by_account:
            revenue_by_account[aid] = []
        revenue_by_account[aid].append({
            "name": opp["Name"],
            "amount": opp["Amount"] or 0,
            "close_date": opp["CloseDate"],
        })
    return revenue_by_account


def build_report(members, revenue_by_account):
    """Group members by company and attach revenue."""
    companies = {}
    for m in members:
        account = m.get("Contact", {}).get("Account", {})
        account_id   = account.get("Id")
        account_name = account.get("Name", "Unknown")
        campaign_name = m.get("Campaign", {}).get("Name", "Unknown")
        contact_name  = m.get("Contact", {}).get("Name", "")

        if not account_id:
            continue

        if account_id not in companies:
            companies[account_id] = {
                "name": account_name,
                "campaigns": set(),
                "contacts": set(),
                "revenue": revenue_by_account.get(account_id, []),
            }
        companies[account_id]["campaigns"].add(campaign_name)
        companies[account_id]["contacts"].add(contact_name)

    return list(companies.values())


def format_slack_message(report, month_label):
    """Build the Slack message."""
    influenced     = [c for c in report if c["revenue"]]
    not_yet        = [c for c in report if not c["revenue"]]
    total_revenue  = sum(
        opp["amount"] for c in influenced for opp in c["revenue"]
    )

    lines = [
        f"*📊 Influenced Marketing Report — {month_label}*",
        f"_{len(report)} companies attended campaigns · {len(influenced)} generated revenue_",
        "",
    ]

    if influenced:
        lines.append("*✅ Companies with new revenue*")
        for c in sorted(influenced, key=lambda x: -sum(o["amount"] for o in x["revenue"])):
            rev = sum(o["amount"] for o in c["revenue"])
            campaigns = ", ".join(c["campaigns"])
            lines.append(
                f"> *{c['name']}* — ${rev:,.0f} closed\n"
                f"> _Attended: {campaigns}_"
            )
        lines.append("")

    if not_yet:
        lines.append("*🕐 Attended but no revenue yet*")
        for c in sorted(not_yet, key=lambda x: x["name"]):
            campaigns = ", ".join(c["campaigns"])
            lines.append(f"> *{c['name']}* — _{campaigns}_")
        lines.append("")

    lines.append(f"*Total influenced revenue: ${total_revenue:,.0f}*")

    return "\n".join(lines)


def post_to_slack(message, webhook_url):
    payload = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        print(f"Slack response: {resp.status}")


def main():
    print("Connecting to Salesforce...")
    sf = connect_to_salesforce()

    print("Fetching campaign members from last 30 days...")
    members = get_recent_campaign_members(sf)
    print(f"Found {len(members)} campaign member records.")

    account_ids = list({
        m["Contact"]["Account"]["Id"]
        for m in members
        if m.get("Contact", {}).get("Account", {}).get("Id")
    })

    lookback_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    print(f"Checking revenue for {len(account_ids)} companies...")
    revenue_by_account = get_revenue_for_accounts(sf, account_ids, lookback_date)

    report = build_report(members, revenue_by_account)
    month_label = datetime.now().strftime("%B %Y")
    message = format_slack_message(report, month_label)

    print("\n--- Slack Message Preview ---")
    print(message)
    print("----------------------------\n")

    webhook_url = os.environ["SLACK_WEBHOOK_URL"]
    print("Posting to Slack...")
    post_to_slack(message, webhook_url)
    print("Done!")


if __name__ == "__main__":
    main()
