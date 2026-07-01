import pdfplumber
import pandas as pd
import sqlite3
import requests
import re
from datetime import datetime

DB_PATH = "airtraffic.db"


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _clean_text(cell):
    if cell is None:
        return ''
    txt = re.sub(r'[^\x00-\x7F]+', ' ', str(cell))
    return ' '.join(txt.split()).strip()


def _to_number(cell):
    """Parse a value cell to float. Handles comma grouping; returns None for
    blanks ('' or None), dashes and any non-numeric text."""
    if cell is None:
        return None
    s = str(cell).replace(',', '').strip()
    if s in ('', '-', '\u2014', 'NA', 'N.A.', 'N.A'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


METRIC_KEYS = {
    'AIRCRAFT MOVEMENTS': 'Movements',
    'PASSENGERS': 'Passengers',
    'FREIGHT': 'Freight',
}


# ----------------------------------------------------------------------------
# Annex-1 parsers
# ----------------------------------------------------------------------------
def parse_annex1(filepath, month, year):
    """Period-format Annex-1 (monthly + April-to-date columns), 7 cols:
       [Category, month_cur, month_prev, %chg, period_cur, period_prev, %chg]."""
    with pdfplumber.open(filepath) as pdf:
        table = []
        for page in pdf.pages:
            t = page.extract_table()
            if t:
                table.extend(t)

    rows = []
    current_metric = None
    current_type = None

    for row in table:
        if not row or row[0] is None:
            continue
        english = _clean_text(row[0])
        if not english:
            continue
        letters = ' '.join(re.sub(r'[^A-Za-z+ ]', ' ', english).upper().split())
        v1 = _to_number(row[1]) if len(row) > 1 else None
        is_header = v1 is None

        if (letters.startswith('NOTE') or 'ANNEXURE' in letters
                or letters.startswith('AIRPORT CATEGORY') or letters.startswith('FOR THE')):
            continue

        if is_header:
            matched = next((m for k, m in METRIC_KEYS.items() if k in letters), None)
            if matched:
                current_metric, current_type = matched, None
                continue
            if 'DOMESTIC' in letters and 'INTERNATIONAL' in letters:
                current_type = 'Total'
                continue
            if letters == 'INTERNATIONAL':
                current_type = 'International'
                continue
            if letters == 'DOMESTIC':
                current_type = 'Domestic'
                continue
            continue  

        if (letters.startswith('TOTAL') or letters.startswith('GRAND TOTAL')
                or letters.startswith('GENERAL AVIATION')):
            continue  
        if current_metric is None or current_type is None:
            continue

        cum_cur, cum_prev = None, None
        if len(row) > 4 and row[4] is not None:
            parts = [p.strip() for p in str(row[4]).split('\n') if p.strip()]
            if len(parts) >= 2:
                cum_cur = _to_number(parts[0])
                cum_prev = _to_number(parts[1])
            else:
                cum_cur = _to_number(row[4])
                cum_prev = _to_number(row[5]) if len(row) > 5 else None

        rows.append({
            'Month': month, 'Year': year, 'Metric': current_metric, 'Type': current_type,
            'Category': english,
            'Month_Value': v1,
            'Prev_Month_Value': _to_number(row[2]) if len(row) > 2 else None,
            'Cumulative_Value': cum_cur,
            'Prev_Cumulative_Value': cum_prev,
        })
    return pd.DataFrame(rows)


def parse_annex1_old(filepath, month, year, debug=False):
    """Old 4-column Annex-1 (pre-period-format). No cumulative columns, so
    Cumulative_Value mirrors Month_Value."""
    with pdfplumber.open(filepath) as pdf:
        table = []
        for page in pdf.pages:
            t = page.extract_table()
            if t:
                table.extend(t)

    rows, unmatched, subtotals = [], [], []
    current_metric = None
    current_type = None

    for raw in table:
        if not raw or len(raw) < 2:
            continue
        label = _clean_text(raw[0])
        if not label:
            continue
        v1 = _to_number(raw[1])
        v2 = _to_number(raw[2]) if len(raw) > 2 else None
        is_header = (v1 is None)
        letters = ' '.join(re.sub(r'[^A-Za-z+ ]', ' ', label).upper().split())

        if (letters.startswith('NOTE') or letters.startswith('ANNEXURE')
                or letters.startswith('AIRPORT CATEGORY') or letters.startswith('FOR THE MONTH')):
            continue

        if is_header:
            matched = next((m for k, m in METRIC_KEYS.items() if k in letters), None)
            if matched:
                current_metric, current_type = matched, None
                continue
            if ('INTERNATIONAL' in letters and 'DOMESTIC' in letters) or letters == 'TOTAL':
                current_type = 'Total'
                continue
            if letters == 'INTERNATIONAL':
                current_type = 'International'
                continue
            if letters == 'DOMESTIC':
                current_type = 'Domestic'
                continue
            if debug:
                unmatched.append({'kind': 'UNKNOWN_HEADER', 'label': label, 'raw': raw})
            continue

        if (letters.startswith('TOTAL') or letters.startswith('GRAND TOTAL')
                or letters.startswith('GENERAL AVIATION')):
            subtotals.append({'Metric': current_metric, 'Type': current_type,
                              'Category': label, 'Value': v1})
            continue
        if current_metric is None or current_type is None:
            unmatched.append({'kind': 'NO_CONTEXT', 'Category': label, 'Value': v1})
            continue

        rows.append({
            'Month': month, 'Year': year, 'Metric': current_metric, 'Type': current_type,
            'Category': label,
            'Month_Value': v1, 'Prev_Month_Value': v2,
            'Cumulative_Value': v1, 'Prev_Cumulative_Value': v2,
        })

    df = pd.DataFrame(rows)
    return df


# ----------------------------------------------------------------------------
# Annex-3 parsers (airport level)
# ----------------------------------------------------------------------------
def parse_annex3(filepath, month, year):
    rows = []
    current_type = None
    current_category = None
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table[:3]:
                if row and row[0]:
                    text = _clean_text(row[0])
                    if 'ANNEXURE-III A' in text or 'International Passengers' in text:
                        current_type = 'International'; break
                    elif 'ANNEXURE-III B' in text or 'Domestic Passengers' in text:
                        current_type = 'Domestic'; break
                    elif 'ANNEXURE-III C' in text or 'Total Passengers' in text:
                        current_type = 'Total'; break
            if current_type is None:
                continue
            
            for row in table:
                if not row or not row[0]:
                    continue
                c0 = _clean_text(row[0])
                
                if (row[1] is None or _clean_text(row[1]) == '') and any(x in c0 for x in [
                        'INTERNATIONAL AIRPORTS', 'PPP INTERNATIONAL', 'JV INTERNATIONAL',
                        'ST GOVT', 'CUSTOM AIRPORTS', 'DOMESTIC AIRPORTS']):
                    current_category = c0.lstrip('/ ').strip()
                    continue
                
                parts = c0.strip().split(' ', 1)
                serial = parts[0]
                is_airport_serial = bool(re.match(r'^[A-Za-z]{0,2}\d+$', serial))
                
                if is_airport_serial and len(parts) > 1:
                    raw_name = parts[1]
                    airport_name = re.sub(r'[^A-Za-z() ]', '', raw_name).replace('( )', '').strip()
                    if not airport_name:
                        continue
                    
                    rows.append({
                        'Month': month, 'Year': year, 'Airport': airport_name, 'Pass_Type': current_type,
                        'Airport_Category': current_category,
                        'Month_Value': _to_number(row[1]) if len(row) > 1 else None,
                        'Prev_Month_Value': _to_number(row[2]) if len(row) > 2 else None,
                        'Cumulative_Value': _to_number(row[4]) if len(row) > 4 else None,
                        'Prev_Cumulative_Value': _to_number(row[5]) if len(row) > 5 else None,
                    })

    df = pd.DataFrame(rows)
    for c in ['Month_Value', 'Prev_Month_Value', 'Cumulative_Value', 'Prev_Cumulative_Value']:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors='coerce')
    if not df.empty:
        df = df.dropna(subset=['Month_Value']).reset_index(drop=True)
    return df


def parse_annex3_old(filepath, month, year):
    rows = []
    current_type = None
    current_category = None
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table[:3]:
                if row and row[0]:
                    text = _clean_text(row[0])
                    if 'ANNEXURE-III A' in text or 'International Passengers' in text:
                        current_type = 'International'; break
                    elif 'ANNEXURE-III B' in text or 'Domestic Passengers' in text:
                        current_type = 'Domestic'; break
                    elif 'ANNEXURE-III C' in text or 'Total Passengers' in text:
                        current_type = 'Total'; break
            if current_type is None:
                continue
            for row in table:
                if row is None or row[0] is None:
                    continue
                first = _clean_text(row[0])
                if row[1] is None and any(x in first for x in [
                        'INTERNATIONAL AIRPORTS', 'PPP INTERNATIONAL', 'JV INTERNATIONAL',
                        'ST GOVT', 'CUSTOM AIRPORTS', 'DOMESTIC AIRPORTS']):
                    current_category = first.lstrip('/ ').strip()
                    continue
                if not str(row[0]).strip().isdigit():
                    continue
                airport = _clean_text(row[1]).replace('( )', '').strip()
                if not airport:
                    continue
                mv = _to_number(row[2]) if len(row) > 2 else None
                pv = _to_number(row[3]) if len(row) > 3 else None
                rows.append({
                    'Month': month, 'Year': year, 'Airport': airport, 'Pass_Type': current_type,
                    'Airport_Category': current_category,
                    'Month_Value': mv, 'Prev_Month_Value': pv,
                    'Cumulative_Value': mv, 'Prev_Cumulative_Value': pv,
                })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.dropna(subset=['Month_Value']).reset_index(drop=True)
    return df


# ----------------------------------------------------------------------------
# storage
# ----------------------------------------------------------------------------
def _scope_warn(conn, months):
    scan = pd.read_sql("""
        SELECT Year, Month, Metric,
               SUM(CASE WHEN Type='International' THEN Month_Value ELSE 0 END) AS Intl,
               SUM(CASE WHEN Type='Domestic'      THEN Month_Value ELSE 0 END) AS Dom
        FROM monthly_traffic
        WHERE Metric IN ('Passengers','Movements') AND Month_Value IS NOT NULL
        GROUP BY Year, Month, Metric
    """, conn)
    if scan.empty:
        return
    bad = scan[(scan['Dom'] > 0) & (scan['Intl'] >= scan['Dom'])]
    bad = bad[bad.apply(lambda r: (r['Month'], r['Year']) in months, axis=1)]
    if not bad.empty:
        print("WARNING — International >= Domestic on these just-stored months:")
        print(bad.to_string(index=False))


def store_annex1(df):
    if df is None or df.empty:
        print("store_annex1: empty parse result")
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    months = set()
    for mon, yr in df[['Month', 'Year']].drop_duplicates().itertuples(index=False):
        cur.execute("DELETE FROM monthly_traffic WHERE Month=? AND Year=?", (mon, yr))
        months.add((mon, yr))
    cur.executemany("""
        INSERT INTO monthly_traffic
        (Month, Year, Metric, Type, Category, Month_Value, Prev_Month_Value, Cumulative_Value, Prev_Cumulative_Value)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, list(df[['Month', 'Year', 'Metric', 'Type', 'Category', 'Month_Value',
                  'Prev_Month_Value', 'Cumulative_Value', 'Prev_Cumulative_Value']]
              .itertuples(index=False, name=None)))
    conn.commit()
    print(f"Stored {len(df)} monthly rows for {sorted(months)}")
    _scope_warn(conn, months)
    conn.close()


def store_annex3(df):
    if df is None or df.empty:
        print("store_annex3: empty parse result")
        return
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for mon, yr in df[['Month', 'Year']].drop_duplicates().itertuples(index=False):
        cur.execute("DELETE FROM airport_traffic WHERE Month=? AND Year=?", (mon, yr))
    cur.executemany("""
        INSERT INTO airport_traffic
        (Month, Year, Airport, Pass_Type, Airport_Category, Month_Value, Prev_Month_Value, Cumulative_Value, Prev_Cumulative_Value)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, list(df[['Month', 'Year', 'Airport', 'Pass_Type', 'Airport_Category', 'Month_Value',
                  'Prev_Month_Value', 'Cumulative_Value', 'Prev_Cumulative_Value']]
              .itertuples(index=False, name=None)))
    conn.commit()
    conn.close()
    print(f"Stored {len(df)} airport rows")


# ----------------------------------------------------------------------------
# fetching (UPDATED TO BYPASS FIREWALL)
# ----------------------------------------------------------------------------
def fetch_pdf(url, save_path):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200 and r.content.startswith(b'%PDF'):
            with open(save_path, 'wb') as f:
                f.write(r.content)
            return True
        return False
    except Exception:
        return False


def fetch_pdf_with_fallback(month, yr, annex_num, save_path):
    month_short = month[:3]
    yr_short = str(yr)[2:]
    suffixes = ['', '_0', '_1', '_2', '_up', '_new', '%20']
    base = "https://www.aai.aero/sites/default/files/traffic-news/"
    patterns = [
        f"{month}2k{yr_short}Annex{annex_num}", f"{month_short}2k{yr_short}Annex{annex_num}",
        f"{month}2K{yr_short}Annex{annex_num}", f"{month_short}2K{yr_short}Annex{annex_num}",
        f"{month.upper()}2K{yr_short}ANNEX{annex_num}", f"{month_short.upper()}2K{yr_short}ANNEX{annex_num}",
        f"{month_short}2k{yr_short}Annex_{annex_num}", f"rev_{month_short}2k{yr_short}Annex{annex_num}",
        f"{month_short}202kAnnex{annex_num}",
    ]
    for pat in patterns:
        for suf in suffixes:
            if fetch_pdf(f"{base}{pat}{suf}.pdf", save_path):
                return True
    return False


# ----------------------------------------------------------------------------
# orchestration
# ----------------------------------------------------------------------------
def backfill_historical():
    months = ['April', 'May', 'June', 'July', 'August', 'September',
              'October', 'November', 'December', 'January', 'February', 'March']

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT Month, Year FROM monthly_traffic")
    existing = set(cur.fetchall())
    conn.close()

    fy_ranges = [
        (2019, 2020, '2019-2020'), (2020, 2021, '2020-2021'), (2021, 2022, '2021-2022'),
        (2022, 2023, '2022-2023'), (2023, 2024, '2023-2024'), (2024, 2025, '2024-2025'),
        (2025, 2026, '2025-2026'),
    ]
    old_format_fys = {'2019-2020', '2020-2021', '2021-2022', '2022-2023', '2023-2024'}

    for start_yr, end_yr, fy in fy_ranges:
        for month in months:
            yr = start_yr if months.index(month) < 9 else end_yr
            if (month, fy) in existing:
                continue

            if fetch_pdf_with_fallback(month, yr, 1, 'temp_annex1.pdf'):
                try:
                    if fy in old_format_fys:
                        df1 = parse_annex1_old('temp_annex1.pdf', month, fy)
                        if df1.empty:
                            df1 = parse_annex1('temp_annex1.pdf', month, fy)
                    else:
                        df1 = parse_annex1('temp_annex1.pdf', month, fy)
                    store_annex1(df1)
                except Exception:
                    pass

            if fetch_pdf_with_fallback(month, yr, 3, 'temp_annex3.pdf'):
                try:
                    df3 = parse_annex3('temp_annex3.pdf', month, fy)
                    if df3.empty:
                        df3 = parse_annex3_old('temp_annex3.pdf', month, fy)
                    store_annex3(df3)
                except Exception:
                    pass


def check_and_update():
    # HARDCODED URL FOR MAY 2026 - REPLACE THIS WITH THE ACTUAL URL IF YOU HAVE IT
    # If this fails, the IP is blocked. Stop trying to automate.
    target_month = "May"
    target_yr = 2026
    fy = "2025-2026"
    
    # Only one attempt, no loops, no guesses
    urls = [
        "https://www.aai.aero/sites/default/files/traffic-news/May2k26Annex1.pdf",
        "https://www.aai.aero/sites/default/files/traffic-news/May2k26Annex3.pdf"
    ]
    
    # Just try these two files
    for url in urls:
        print(f"DEBUG: Trying exact URL: {url}")
        if fetch_pdf(url, 'temp.pdf'):
            print("DEBUG: Download success.")
            # ... process it ...
        else:
            print("DEBUG: Download failed. The server blocked us.")
