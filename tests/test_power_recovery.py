import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from power_recovery import COUNTIES, normalize, persist, read_csv

def payload(out=100, total=1000):
    return {'features': [{'attributes': {
        'COUNTY': name, 'CUSTOMERS_TOTAL': total, 'CUSTOMERS_OUT': out,
        'GIS_OUT': out/total*100, 'UPDATED': 1791650000000,
    }} for name in COUNTIES]}

class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.when = datetime(2026, 10, 11, 4, 0, tzinfo=timezone.utc)
    def test_counties_and_weighted_total(self):
        rows, region = normalize(payload(), self.when)
        self.assertEqual(len(rows), 12)
        self.assertEqual(region['customers_out'], 1200)
        self.assertEqual(region['percentage_with_power'], 90.0)
    def test_missing_county_rejected(self):
        x = payload()
        x['features'].pop()
        with self.assertRaisesRegex(ValueError, 'Missing counties'):
            normalize(x, self.when)
    def test_invalid_count_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Implausible'):
            normalize(payload(out=1100), self.when)
    def test_history_comparison(self):
        with TemporaryDirectory() as d:
            rows, reg = normalize(payload(), self.when)
            persist(Path(d), rows, reg)
            later = datetime(2026, 10, 11, 5, tzinfo=timezone.utc)
            rows2, reg2 = normalize(payload(out=60), later)
            self.assertEqual(persist(Path(d), rows2, reg2), 2)
            self.assertEqual(len(read_csv(Path(d)/'county_history.csv')), 24)

if __name__ == '__main__':
    unittest.main()
