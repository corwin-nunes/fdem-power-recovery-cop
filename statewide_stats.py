"""Calculate a statewide snapshot from the 67 records returned by FDEM.

We deliberately check coverage and record validity so the statewide total is not
silently calculated from an incomplete response.
"""
from datetime import timezone

EXPECTED_COUNTIES = 67

def summarize_statewide(payload, captured_at):
    if not isinstance(payload, dict) or payload.get('error') or payload.get('exceededTransferLimit'):
        raise ValueError('FDEM returned an error or partial statewide response')
    features = payload.get('features')
    if not isinstance(features, list) or len(features) != EXPECTED_COUNTIES:
        raise ValueError(f'Expected {EXPECTED_COUNTIES} statewide county records; received {len(features) if isinstance(features, list) else "none"}')
    seen = set()
    total, out, stale = 0, 0, 0
    latest_update = None
    for feature in features:
        attrs = feature.get('attributes', {})
        county = str(attrs.get('COUNTY') or '').strip().upper()
        if not county or county in seen:
            raise ValueError(f'Blank or duplicate county in statewide records: {county!r}')
        seen.add(county)
        served = attrs.get('CUSTOMERS_TOTAL')
        customers_out = attrs.get('CUSTOMERS_OUT')
        if any(isinstance(x, bool) or not isinstance(x, int) for x in (served, customers_out)):
            raise ValueError(f'Invalid numeric fields for {county}')
        if served <= 0 or not 0 <= customers_out <= served:
            raise ValueError(f'Implausible customers out or served for {county}')
        updated = attrs.get('UPDATED')
        if isinstance(updated, bool) or not isinstance(updated, (int, float)):
            raise ValueError(f'Invalid source timestamp for {county}')
        updated_at = __import__('datetime').datetime.fromtimestamp(updated / 1000, timezone.utc)
        if latest_update is None or updated_at > latest_update:
            latest_update = updated_at
        if (captured_at - updated_at).total_seconds() > 6 * 3600:
            stale += 1
        total += served
        out += customers_out
    iso = lambda dt: dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    return {
        'captured_at_utc': iso(captured_at),
        'county_count': len(seen),
        'customers_total': total,
        'customers_out': out,
        'customers_with_power': total - out,
        'percentage_out': round(100 * out / total, 4),
        'percentage_with_power': round(100 * (total - out) / total, 4),
        'stale_count': stale,
        'latest_source_updated_at_utc': iso(latest_update),
    }
