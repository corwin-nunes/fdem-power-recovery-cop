#!/usr/bin/env python3
"""Capture one snapshot of 12 Panhandle counties from FDEM ArcGIS FeatureServer.

stdlib only. Writes history to docs/data (committed by GitHub Actions).
This measures NET power availability, not utility-confirmed restored customers.
"""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from statewide_stats import summarize_statewide

FEATURE_URL = ('https://services.arcgis.com/3wFbqsFPLeKqOlIK/arcgis/rest/'
               'services/Power_Outages_Hosted/FeatureServer/1/query')
COUNTIES = (
    'ESCAMBIA', 'SANTA ROSA', 'OKALOOSA', 'WALTON', 'HOLMES',
    'WASHINGTON', 'JACKSON', 'BAY', 'GADSDEN', 'LEON', 'LIBERTY', 'CALHOUN',
)
FIELD_NAMES = ('COUNTY', 'CUSTOMERS_TOTAL', 'CUSTOMERS_OUT', 'GIS_OUT', 'UPDATED')
COUNTY_HEADERS = (
    'captured_at_utc','county','customers_total','customers_out',
    'customers_with_power','percentage_out','percentage_with_power',
    'source_updated_at_utc','source_age_hours',
)
REGIONAL_HEADERS = (
    'captured_at_utc','county_count','customers_total','customers_out',
    'customers_with_power','percentage_out','percentage_with_power','stale_count',
)

def timestamp(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')

def parse_time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Invalid source UPDATED timestamp')
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)

def fetch_arcgis():
    params = urlencode({
        'where': '1=1', 'outFields': ','.join(FIELD_NAMES),
        'returnGeometry': 'false', 'f': 'json', 'resultRecordCount': 200,
    })
    req = Request(FEATURE_URL+'?'+params, headers={
        'User-Agent': 'fdem-power-recovery-cop/1.0 (hourly public GIS monitoring)',
        'Accept': 'application/json',
    })
    for i in range(3):
        try:
            with urlopen(req, timeout=35) as r:
                raw = r.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError('Response is unexpectedly large')
            return json.loads(raw.decode('utf-8'))
        except (HTTPError, URLError, TimeoutError) as exc:
            if i == 2:
                raise RuntimeError(f'FDEM query unavailable after retries: {exc}') from exc
            time.sleep(2 * (i + 1))

def normalize(payload, captured_at):
    if not isinstance(payload, dict) or 'error' in payload or payload.get('exceededTransferLimit'):
        raise ValueError(f'FDEM query returned an error or incomplete result: {str(payload)[:250]}')
    features = payload.get('features')
    if not isinstance(features, list):
        raise ValueError('FDEM response missing features list')
    records = {}
    for feature in features:
        a = feature.get('attributes', {})
        county = str(a.get('COUNTY', '')).strip().upper()
        if county not in COUNTIES:
            continue
        if county in records:
            raise ValueError(f'Duplicate county: {county}')
        total, out = a.get('CUSTOMERS_TOTAL'), a.get('CUSTOMERS_OUT')
        if any(isinstance(x, bool) or not isinstance(x, int) for x in (total, out)):
            raise ValueError(f'Missing/invalid counts for {county}')
        if total <= 0 or not (0 <= out <= total):
            raise ValueError(f'Implausible outage counts for {county}: {out}/{total}')
        updated = parse_time(a.get('UPDATED'))
        age = round((captured_at - updated).total_seconds()/3600, 2)
        pct_out = round(out/total*100, 4)
        pct_with = round((total-out)/total*100, 4)
        records[county] = {
            'captured_at_utc': timestamp(captured_at),
            'county': county,
            'customers_total': total,
            'customers_out': out,
            'customers_with_power': total-out,
            'percentage_out': pct_out,
            'percentage_with_power': pct_with,
            'source_updated_at_utc': timestamp(updated),
            'source_age_hours': age,
        }
    missing = set(COUNTIES) - set(records)
    if missing:
        raise ValueError('Missing counties: '+', '.join(sorted(missing)))
    ordered = [records[name] for name in COUNTIES]
    total = sum(x['customers_total'] for x in ordered)
    out = sum(x['customers_out'] for x in ordered)
    regional = {
        'captured_at_utc': timestamp(captured_at),
        'county_count': len(ordered),
        'customers_total': total,
        'customers_out': out,
        'customers_with_power': total-out,
        'percentage_out': round(out/total*100, 4),
        'percentage_with_power': round((total-out)/total*100, 4),
        'stale_count': sum(x['source_age_hours'] > 6 for x in ordered),
    }
    return ordered, regional

def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))

def write_atomic(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False, newline='') as f:
        f.write(contents)
        temp = Path(f.name)
    temp.replace(path)

def csv_text(rows, headers):
    from io import StringIO
    s = StringIO()
    writer = csv.DictWriter(s, fieldnames=headers, lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, '') for key in headers})
    return s.getvalue()

def persist(out_dir, counties, regional, statewide=None):
    county_csv = out_dir/'county_history.csv'
    regional_csv = out_dir/'regional_history.csv'
    old_county = read_csv(county_csv)
    old_regional = read_csv(regional_csv)
    last_at = old_regional[-1]['captured_at_utc'] if old_regional else None
    this_at = regional['captured_at_utc']
    if last_at is not None and this_at <= last_at:
        raise ValueError('New snapshot timestamp must be later than last historical capture')
    previous = {}
    if last_at:
        previous = {r['county']: r for r in old_county if r['captured_at_utc'] == last_at}
    annotated = []
    for row in counties:
        p = previous.get(row['county'])
        old_out = int(p['customers_out']) if p else None
        item = dict(row)
        item['previous_percentage_with_power'] = float(p['percentage_with_power']) if p else None
        item['previous_customers_out'] = old_out
        item['net_customers_out_reduction'] = (old_out-row['customers_out']) if p else None
        item['source_is_stale'] = row['source_age_hours'] > 6
        annotated.append(item)
    regional_rows = old_regional+[regional]
    county_rows = old_county+counties
    write_atomic(regional_csv, csv_text(regional_rows, REGIONAL_HEADERS))
    write_atomic(county_csv, csv_text(county_rows, COUNTY_HEADERS))
    write_atomic(out_dir/'regional_history.json', json.dumps([
        {
            'captured_at_utc': x['captured_at_utc'],
            'percentage_with_power': float(x['percentage_with_power']),
            'customers_out': int(x['customers_out']),
            'stale_count': int(x['stale_count']),
        } for x in regional_rows
    ], indent=2)+'\n')
    if statewide is not None:
        statewide_path = out_dir/'statewide_history.csv'
        headers = ('captured_at_utc','county_count','customers_total','customers_out','customers_with_power','percentage_out','percentage_with_power','stale_count','latest_source_updated_at_utc')
        prior_statewide = read_csv(statewide_path)
        if prior_statewide and statewide['captured_at_utc'] <= prior_statewide[-1]['captured_at_utc']:
            raise ValueError('Statewide snapshot is not newer than previous record')
        write_atomic(statewide_path, csv_text(prior_statewide+[statewide], headers))
    write_atomic(out_dir/'latest.json', json.dumps({
        'statewide': statewide,
        'source': 'FDEM Power_Outages_Hosted FeatureServer/1',
        'scope': '12 selected Panhandle counties; utility reporting may be asynchronous',
        'retrieved_at_utc': regional['captured_at_utc'],
        'regional': regional,
        'counties': annotated,
        'cautions': [
            'Percentage with power is total less currently out; it is not a confirmed utility restoration-event count.',
            'County source UPDATED timestamps vary; stale records must not be treated as current updates.',
        ],
    }, indent=2)+'\n')
    return len(regional_rows)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-file', type=Path, help='Use a saved FDEM JSON response, for testing only')
    p.add_argument('--out-dir', type=Path, default=Path('docs/data'))
    p.add_argument('--captured-at', help='ISO time with explicit timezone, for testing only')
    args = p.parse_args()
    now = datetime.fromisoformat(args.captured_at.replace('Z', '+00:00')) if args.captured_at else datetime.now(timezone.utc)
    if now.tzinfo is None:
        p.error('--captured-at requires an explicit timezone')
    payload = json.loads(args.input_file.read_text(encoding='utf-8')) if args.input_file else fetch_arcgis()
    counties, regional = normalize(payload, now)
    statewide = summarize_statewide(payload, now)
    count = persist(args.out_dir, counties, regional, statewide)
    print(f'Statewide: {statewide["customers_out"]:,} customers out across {statewide["county_count"]} counties, {statewide["percentage_out"]:.3f}% of accounts.')
    print(f'Success: {regional["county_count"]} counties, {regional["customers_out"]:,} customers out, '
          f'{regional["percentage_with_power"]:.1f}% with power; '
          f'{regional["stale_count"]} counties with source data >6h old; {count} snapshots saved.')
    print('Captured (UTC):', timestamp(now), '| ET:', now.astimezone(ZoneInfo('America/New_York')).isoformat())

if __name__ == '__main__':
    main()
