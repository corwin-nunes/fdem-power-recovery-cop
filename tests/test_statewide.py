import unittest
from datetime import datetime, timezone
from statewide_stats import summarize_statewide

NOW = datetime(2026, 10, 11, 6, 0, tzinfo=timezone.utc)
STAMP = int(NOW.timestamp() * 1000)

def sample(size=67):
    return {'features': [{'attributes': {
        'COUNTY': f'COUNTY{i:02d}', 'CUSTOMERS_TOTAL': 100,
        'CUSTOMERS_OUT': i % 2 * 10, 'UPDATED': STAMP,
    }} for i in range(size)]}

class StatewideTests(unittest.TestCase):
    def test_counts_all_67(self):
        data = summarize_statewide(sample(), NOW)
        self.assertEqual(data['county_count'], 67)
        self.assertEqual(data['customers_total'], 6700)
        self.assertEqual(data['customers_out'], 330)
        self.assertAlmostEqual(data['percentage_out'], 330 / 6700 * 100, places=3)
    def test_rejects_partial_feed(self):
        with self.assertRaisesRegex(ValueError, 'Expected 67'):
            summarize_statewide(sample(66), NOW)
    def test_rejects_duplicate_county(self):
        payload = sample()
        payload['features'][-1]['attributes']['COUNTY'] = 'COUNTY00'
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            summarize_statewide(payload, NOW)
    def test_rejects_invalid_values(self):
        payload = sample()
        payload['features'][0]['attributes']['CUSTOMERS_OUT'] = 999
        with self.assertRaisesRegex(ValueError, 'Implausible'):
            summarize_statewide(payload, NOW)

if __name__ == '__main__':
    unittest.main()
