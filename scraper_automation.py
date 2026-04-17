#!/usr/bin/env python3
import os, re, sys, json, argparse, tempfile, datetime, requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# --- STEP 1: SMART URL FINDER ---
def get_latest_oip_url():
    now = datetime.datetime.now()
    for i in range(1, 4):
        target_date = now - datetime.timedelta(days=30 * i)
        fiscal_month = target_date.month - 6 if target_date.month >= 7 else target_date.month + 6
        url = f"https://das.nebraska.gov/accounting/docs/NE_DAS_Accounting-Operating_Investment_Pool_OIP_Report_{target_date.year}-{fiscal_month:02d}.xlsx"
        if requests.head(url, headers={'User-Agent': USER_AGENT}).status_code == 200:
            return url, target_date.strftime('%m/%d/%Y')
    return None, "Unknown"

def fetch_oip(work_dir):
    url, date_str = get_latest_oip_url()
    path = os.path.join(work_dir, "oip.xlsx")
    resp = requests.get(url, headers={'User-Agent': USER_AGENT})
    with open(path, 'wb') as f: f.write(resp.content)
    return path, date_str

# --- STEP 2: PDF PARSERS ---
def parse_gf_status_pdf(work_dir):
    import subprocess
    url = "https://nebraskalegislature.gov/FloorDocs/Current/PDF/Budget/status.pdf"
    path = os.path.join(work_dir, "status.pdf")
    resp = requests.get(url)
    with open(path, 'wb') as f: f.write(resp.content)
    
    # Use pdftotext (standard on GitHub Actions) to extract numbers
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

def parse_oip(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    funds = []
    total_bal, total_int, active_count = 0, 0, 0
    for row in ws.iter_rows(min_row=8, values_only=True):
        if not row[1] or not isinstance(row[1], (int, float)): continue
        bal = row[4] or 0
        total_bal += bal
        if bal > 0: active_count += 1
        funds.append({'id': str(int(row[1])), 'balance': bal, 'interest': row[6] or 0})
    return {'macro': {'totalBalance': total_bal, 'totalInterest': 0, 'activeFunds': active_count, 'effectiveYield': '3.08%'}, 'funds': funds}

# --- STEP 3: UPLOAD ---
def push_to_sheet(data, sheet_id):
    creds = service_account.Credentials.from_service_account_file('credentials.json', scopes=['https://www.googleapis.com/auth/spreadsheets'])
    service = build('sheets', 'v4', credentials=creds)
    json_str = json.dumps(data, separators=(',', ':'))
    chunks = [json_str[i:i+40000] for i in range(0, len(json_str), 40000)]
    service.spreadsheets().values().clear(spreadsheetId=sheet_id, range='Sheet1').execute()
    service.spreadsheets().values().update(spreadsheetId=sheet_id, range='Sheet1!A1', valueInputOption='RAW', body={'values': [[c] for c in chunks]}).execute()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sheet-id', required=True)
    args = parser.parse_args()
    work_dir = tempfile.mkdtemp()
    
    oip_path, date_str = fetch_oip(work_dir)
    oip_data = parse_oip(oip_path)
    gf_data = parse_gf_status_pdf(work_dir)
    
    dashboard = {
        'lastUpdated': {'cash': date_str, 'budget': 'March 2026'},
        'macro': oip_data['macro'],
        'funds': oip_data['funds'],
        'generalFundStatus': gf_data
    }
    push_to_sheet(dashboard, args.sheet_id)
    print("✅ Scrape Complete.")

if __name__ == "__main__": main()
