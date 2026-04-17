#!/usr/bin/env python3
import os, re, sys, json, argparse, tempfile, datetime, requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ─────────────────────────────────────────────
# STEP 1: URL LOGIC (FISCAL MONTH FIX)
# ─────────────────────────────────────────────

def get_latest_oip_url():
    now = datetime.datetime.now()
    # Try the last 3 months to ensure we find a finalized report
    for i in range(1, 4):
        target_date = now - datetime.timedelta(days=30 * i)
        cal_month = target_date.month
        cal_year = target_date.year
        
        # NE Fiscal: July=01, March=09. Corrects the Sep vs Mar bug.
        fiscal_month = cal_month - 6 if cal_month >= 7 else cal_month + 6
        fm_str = f"{fiscal_month:02d}"
        
        url = f"https://das.nebraska.gov/accounting/docs/NE_DAS_Accounting-Operating_Investment_Pool_OIP_Report_{cal_year}-{fm_str}.xlsx"
        try:
            if requests.head(url, headers={'User-Agent': USER_AGENT}, timeout=5).status_code == 200:
                return url, target_date.strftime('%m/%d/%Y')
        except: continue
    return None, "Unknown"

# ─────────────────────────────────────────────
# STEP 2: FETCHERS
# ─────────────────────────────────────────────

def fetch_oip(work_dir):
    url, date_str = get_latest_oip_url()
    if not url: return None, "Unknown"
    path = os.path.join(work_dir, "oip.xlsx")
    resp = requests.get(url, headers={'User-Agent': USER_AGENT})
    with open(path, 'wb') as f: f.write(resp.content)
    return path, date_str

def fetch_gf_status(work_dir):
    url = "https://nebraskalegislature.gov/FloorDocs/Current/PDF/Budget/status.pdf"
    path = os.path.join(work_dir, "status.pdf")
    try:
        resp = requests.get(url, timeout=20)
        with open(path, 'wb') as f: f.write(resp.content)
        return path
    except: return None

# ─────────────────────────────────────────────
# STEP 3: PARSERS
# ─────────────────────────────────────────────

def parse_gf_status_pdf(path):
    import subprocess
    if not path: return {}
    try:
        # Uses pdftotext (available in GitHub Actions environment)
        text = subprocess.run(['pdftotext', '-layout', path, '-'], capture_output=True, text=True).stdout
        res = {}
        patterns = {
            'netRevenues_FY2526': r'Net Receipts.*?([\d,]+)',
            'appropriations_FY2526': r'Total Appropriations.*?([\d,]+)',
            'beginningBalance_FY2526': r'Beginning Balance.*?([\d,]+)',
            'endingBalance_FY2526': r'Ending Balance.*?\$?\s*([\d,]+)',
            'minimumReserve_variance': r'Variance from 3% Reserve.*?\(([\d,]+)\)',
            'cashReserve_endingBalance': r'Cash Reserve Fund Ending Balance.*?([\d,]+)'
        }
        for k, p in patterns.items():
            m = re.search(p, text, re.I)
            if m:
                val = int(m.group(1).replace(',', ''))
                if '(' in m.group(0): val = -val
                res[k] = val
        return res
    except: return {}

def parse_oip_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    funds = []
    t_bal, active = 0, 0
    # Rows 1-7 are header; data starts at 8
    for row in ws.iter_rows(min_row=8, values_only=True):
        if not row[1] or not isinstance(row[1], (int, float)): continue
        bal = row[4] if isinstance(row[4], (int, float)) else 0
        t_bal += bal
        if bal > 0: active += 1
        funds.append({
            'id': str(int(row[1])), 
            'title': row[3] or f"Fund {int(row[1])}",
            'balance': bal, 
            'interest': row[6] or 0
        })
    return {'macro': {'totalBalance': t_bal, 'activeFunds': active, 'effectiveYield': '3.08%'}, 'funds': funds}

# ─────────────────────────────────────────────
# STEP 4: UPLOAD (CHUNKED FOR GOOGLE SHEETS)
# ─────────────────────────────────────────────

def push_to_sheet(data, sheet_id):
    if not sheet_id: return
    # Load credentials from GitHub Secret file
    creds = service_account.Credentials.from_service_account_file('credentials.json', scopes=['https://www.googleapis.com/auth/spreadsheets'])
    service = build('sheets', 'v4', credentials=creds)
    
    # Minify JSON and chunk into 40k blocks to bypass 50k cell limit
    json_str = json.dumps(data, separators=(',', ':'))
    chunks = [json_str[i:i+40000] for i in range(0, len(json_str), 40000)]
    
    service.spreadsheets().values().clear(spreadsheetId=sheet_id, range='Sheet1').execute()
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id, 
        range='Sheet1!A1', 
        valueInputOption='RAW', 
        body={'values': [[c] for c in chunks]}
    ).execute()

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sheet-id', required=True)
    args = parser.parse_args()
    
    work_dir = tempfile.mkdtemp()
    
    print("Step 1: Fetching OIP...")
    oip_path, date_str = fetch_oip(work_dir)
    
    print("Step 2: Fetching Budget Status...")
    status_path = fetch_gf_status(work_dir)
    
    print("Step 3: Parsing...")
    oip_data = parse_oip_xlsx(oip_path) if oip_path else {'funds':[], 'macro':{}}
    gf_data = parse_gf_status_pdf(status_path)
    
    dashboard = {
        'lastUpdated': {'cash': date_str, 'budget': 'March 2026'},
        'macro': oip_data['macro'],
        'funds': oip_data['funds'],
        'generalFundStatus': gf_data
    }
    
    print("Step 4: Uploading...")
    push_to_sheet(dashboard, args.sheet_id)
    print(f"✅ Complete. Period: {date_str}")

if __name__ == "__main__": main()
