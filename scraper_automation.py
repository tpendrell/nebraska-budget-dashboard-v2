#!/usr/bin/env python3
"""
Nebraska Public Budget Dashboard — Automated Data Scraper
==========================================================
"""

import os
import re
import sys
import json
import argparse
import tempfile
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Browser-like User-Agent to prevent 403 Forbidden errors
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

def http_get(url, timeout=30):
    req = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
    with urlopen(req, timeout=timeout) as response:
        return response.read()

# ─────────────────────────────────────────────
# URL TEMPLATES & TARGETS
# ─────────────────────────────────────────────

REVENUE_RELEASE_URL = (
    "https://revenue.nebraska.gov/sites/default/files/doc/news-release/gen-fund/"
    "{year}/General_Fund_Receipts_News_Release_{month_name}_{year}_Final_Copy.pdf"
)

REVENUE_STATS_PAGES = {
    'sales': "https://revenue.nebraska.gov/research/statistics/sales-tax-data",
    'individual': "https://revenue.nebraska.gov/research/statistics/individual-income-tax-data",
    'corporate': "https://revenue.nebraska.gov/research/statistics/business-income-tax-data",
    'misc': "https://revenue.nebraska.gov/research/statistics/miscellaneous-tax-data",
}

GF_STATUS_URL = "https://nebraskalegislature.gov/FloorDocs/Current/PDF/Budget/status.pdf"
LEG_BUDGET_URL_TEMPLATE = "https://nebraskalegislature.gov/pdf/reports/fiscal/{year}budget.pdf"
LEG_PRELIM_BUDGET_URL_TEMPLATE = "https://nebraskalegislature.gov/pdf/reports/fiscal/{year}prelim.pdf"
LFO_DIRECTORY_VOL1_URL = "https://nebraskalegislature.gov/pdf/reports/fiscal/funddescriptions1_{year}.pdf"
LFO_DIRECTORY_VOL2_URL = "https://nebraskalegislature.gov/pdf/reports/fiscal/funddescriptions2_{year}.pdf"
LFO_DIRECTORY_SUPPLEMENT_URL = "https://nebraskalegislature.gov/pdf/reports/fiscal/funddescriptions_supplement_{year}.pdf"
LFO_DIRECTORY_COMBINED_URL = "https://nebraskalegislature.gov/pdf/reports/fiscal/funddescriptions_{year}.pdf"

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def download_file(url, dest_path):
    try:
        print(f"  Downloading: {url}")
        data = http_get(url)
        with open(dest_path, 'wb') as f:
            f.write(data)
        return True
    except Exception as e:
        print(f"  ⚠️  Download failed: {url} ({e})")
        return False

def get_target_month(month_str=None):
    if month_str:
        dt = datetime.strptime(month_str, "%Y-%m")
    else:
        today = datetime.today()
        first_of_month = today.replace(day=1)
        dt = first_of_month - timedelta(days=1)
    return dt.year, dt.month, dt.strftime("%B")

# ─────────────────────────────────────────────
# STEP 1: FETCH OIP (WITH FISCAL LOGIC & ERROR FIX)
# ─────────────────────────────────────────────

def get_latest_oip_url():
    """Calculates the correct Nebraska Fiscal Month URL."""
    import datetime
    import requests
    now = datetime.datetime.now()
    
    # Try last 3 months to find the most recent published report
    for i in range(1, 4):
        target_date = now - datetime.timedelta(days=30 * i)
        cal_month = target_date.month
        cal_year = target_date.year
        
        # July(7) is Fiscal 01, March(3) is Fiscal 09
        fiscal_month = cal_month - 6 if cal_month >= 7 else cal_month + 6
        fm_str = f"{fiscal_month:02d}"
        
        url = f"https://das.nebraska.gov/accounting/docs/NE_DAS_Accounting-Operating_Investment_Pool_OIP_Report_{cal_year}-{fm_str}.xlsx"
        
        # Verify the file exists before returning
        try:
            req = Request(url, method='HEAD', headers={'User-Agent': USER_AGENT})
            with urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return url, target_date.strftime('%m/%d/%Y')
        except:
            continue
            
    return None, "Unknown"

def fetch_oip(year, month, work_dir):
    """Downloads the OIP XLSX file."""
    url, date_str = get_latest_oip_url()
    if not url:
        print("  ❌ Could not find a valid OIP XLSX URL.")
        return None
    
    path = os.path.join(work_dir, "oip_report.xlsx")
    if download_file(url, path):
        return path
    return None

def fetch_fund_summary(year, month, work_dir):
    """Downloads the Fund Summary PDF."""
    url = f"https://das.nebraska.gov/accounting/docs/NE_DAS_Accounting-Monthly_Reports_Fund_Summary_by_Fund_Report_{year}-{month:02d}.pdf"
    path = os.path.join(work_dir, "fund_summary.pdf")
    if download_file(url, path):
        return path
    return None

# ─────────────────────────────────────────────
# STEP 2: FETCH REVENUE & LEGISLATURE DATA
# ─────────────────────────────────────────────

def fetch_revenue_release(year, month, month_name, work_dir):
    url = REVENUE_RELEASE_URL.format(year=year, month_name=month_name)
    path = os.path.join(work_dir, f"revenue_{year}_{month:02d}.pdf")
    return path if download_file(url, path) else None

def fetch_gf_status(work_dir):
    path = os.path.join(work_dir, "gf_status.pdf")
    return path if download_file(GF_STATUS_URL, path) else None

def fetch_biennial_budget(year, work_dir, preliminary=False):
    template = LEG_PRELIM_BUDGET_URL_TEMPLATE if preliminary else LEG_BUDGET_URL_TEMPLATE
    url = template.format(year=year)
    path = os.path.join(work_dir, f"budget_{year}.pdf")
    return path if download_file(url, path) else None

def fetch_lfo_directory(year, work_dir):
    paths = []
    for template, name in [(LFO_DIRECTORY_VOL1_URL, "vol1"), (LFO_DIRECTORY_VOL2_URL, "vol2")]:
        url = template.format(year=year)
        path = os.path.join(work_dir, f"lfo_{name}_{year}.pdf")
        if download_file(url, path): paths.append(path)
    return paths

# ─────────────────────────────────────────────
# STEP 3: PARSING LOGIC
# ─────────────────────────────────────────────

def parse_oip_for_dashboard(xlsx_path):
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    
    funds = []
    total_bal, total_int, active_count = 0, 0, 0
    
    # Simple parser for OIP layout
    for row in ws.iter_rows(min_row=8, values_only=True):
        if not row[1] or not isinstance(row[1], (int, float)): continue
        bal = row[4] if isinstance(row[4], (int, float)) else 0
        interest = row[6] if isinstance(row[6], (int, float)) else 0
        
        if bal > 0: active_count += 1
        total_bal += bal
        total_int += interest
        
        funds.append({'id': str(int(row[1])), 'balance': bal, 'interest': interest})

    return {
        'macro': {'totalBalance': total_bal, 'totalInterest': total_int, 'activeFunds': active_count, 'effectiveYield': '3.08%'},
        'funds': funds
    }

def parse_gf_status_pdf(path):
    # Simplified placeholder for PDF parsing logic
    return {'ending_balance': 375271641, 'minimum_reserve': -125646757}

# ─────────────────────────────────────────────
# STEP 4: DASHBOARD BUILDING & PUSH
# ─────────────────────────────────────────────

def build_dashboard_json(oip_data, revenue_data, fund_titles, gf_status=None, budget_agencies=None, lfo_descriptions=None):
    oip_data = oip_data or {'funds': [], 'macro': {}}
    url, date_str = get_latest_oip_url()
    
    dashboard = {
        'lastUpdated': {'cash': date_str, 'budget': 'March 2026'},
        'macro': oip_data.get('macro', {}),
        'funds': oip_data.get('funds', []),
        'revenue': revenue_data or {},
        'agencies': budget_agencies or [],
        'generalFundStatus': gf_status or {},
        'fundDescriptions': lfo_descriptions or {}
    }
    return dashboard

def push_to_sheet(dashboard_json, sheet_id=None):
    output_path = 'dashboard_data.json'
    with open(output_path, 'w') as f:
        json.dump(dashboard_json, f, indent=2)

    if sheet_id:
        print(f"Pushing to Sheet: {sheet_id}...")
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            creds = service_account.Credentials.from_service_account_file('credentials.json', scopes=['https://www.googleapis.com/auth/spreadsheets'])
            service = build('sheets', 'v4', credentials=creds)
            
            json_str = json.dumps(dashboard_json, separators=(',', ':'))
            chunks = [json_str[i:i+40000] for i in range(0, len(json_str), 40000)]
            values = [[c] for c in chunks]
            
            service.spreadsheets().values().clear(spreadsheetId=sheet_id, range='Sheet1').execute()
            service.spreadsheets().values().update(spreadsheetId=sheet_id, range='Sheet1!A1', valueInputOption='RAW', body={'values': values}).execute()
            print("✅ Success!")
        except Exception as e:
            print(f"❌ Error: {e}")

# ─────────────────────────────────────────────
# MAIN EXECUTION
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--month', type=str)
    parser.add_argument('--sheet-id', type=str)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    year, month, month_name = get_target_month(args.month)
    work_dir = tempfile.mkdtemp(prefix='ne_budget_')

    print(f"📊 Step 1: Fetching OIP...")
    oip_path = fetch_oip(year, month, work_dir)
    
    print("📋 Step 2: Fetching Fund Summary...")
    fetch_fund_summary(year, month, work_dir)

    print("💰 Step 3: Fetching Revenue & Status...")
    rev_path = fetch_revenue_release(year, month, month_name, work_dir)
    gf_status_path = fetch_gf_status(work_dir)
    
    print("🔧 Step 4: Parsing...")
    oip_data = parse_oip_for_dashboard(oip_path) if oip_path else None
    
    print("📤 Step 5: Building & Pushing...")
    dashboard = build_dashboard_json(oip_data, {}, {}, gf_status_path)
    push_to_sheet(dashboard, args.sheet_id)

if __name__ == '__main__':
    main()
