"""Build parallelism as the memory-for-time knob."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import desktop_workbench as w


class AotParallelismTests(unittest.TestCase):
    def test_an_unset_value_keeps_the_historical_default(self):
        self.assertEqual(w.aot_parallelism(), 3)
        self.assertEqual(w.aot_parallelism(None, quick_build=True), 4)

    def test_an_explicit_setting_wins_over_the_default(self):
        for value in (1, 2, 3, 4):
            self.assertEqual(w.aot_parallelism(value), value)
        # A quick build no longer forces 4 once the user chose a value.
        self.assertEqual(w.aot_parallelism(1, quick_build=True), 1)

    def test_out_of_range_values_are_rejected(self):
        for bad in (0, 5, -1, True, '2', 2.0):
            with self.assertRaises(ValueError):
                w.aot_parallelism(bad)

    def test_validate_request_rejects_a_bad_setting_before_queueing(self):
        base = dict(workspace=str(ROOT), minecraftVersion='26.1.2', loader='fabric',
                    output='/tmp/ncl-parallelism')
        with self.assertRaisesRegex(ValueError, '并行度'):
            w.validate_request({**base, 'aotParallelism': 9})
        with self.assertRaisesRegex(ValueError, '并行度'):
            w.validate_request({**base, 'aotParallelism': 'auto'})

    def test_a_retry_carries_the_current_setting(self):
        # Lowering parallelism is how a user responds to an analysis OOM, so the
        # retry must be able to replace the value recorded by the failed job.
        job = {'request': {'workspace': '/repo', 'aotParallelism': 3}}
        self.assertEqual(w.retry_request(job, ROOT, None, 1)['aotParallelism'], 1)
        # No setting in the GUI leaves the recorded value untouched.
        self.assertEqual(w.retry_request(job, ROOT)['aotParallelism'], 3)
        # An IPA export has no compilation, so its request stays unchanged.
        export = {'request': {'workspace': '/repo', 'existingBuild': '/out'}}
        self.assertEqual(w.retry_request(export, ROOT, None, 1), export['request'])
        with self.assertRaisesRegex(ValueError, '并行度'):
            w.retry_request(job, ROOT, None, 7)


if __name__ == '__main__':
    unittest.main()

