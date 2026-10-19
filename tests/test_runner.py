import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('runner', Path(__file__).resolve().parents[1] / 'app/runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

class RunnerTests(unittest.TestCase):
    def test_regular_email_has_no_slack_section(self):
        source = {'number': 10, 'result': 'UNSTABLE', 'duration': 60000}
        report = {'stats': {'passes': 2, 'failures': 0, 'pending': 1}}
        document, body = runner.render(source, report, 'https://example.com/report', None, [], [])
        self.assertIn('Jenkins report:', body)
        self.assertIn('Complete HTML report attached', body)
        self.assertNotIn('SLACK', body.upper())
        self.assertNotIn('@here', body)
        self.assertIn('QA-MT-SOLAR #10', document)


    def test_filter_deduplicates_and_sorts(self):
        self.assertEqual(runner.spec_filter([{'spec': 'B'}, {'spec': 'A'}, {'spec': 'A'}]), '**/{A,B}Spec.ts')
        self.assertEqual(runner.spec_filter([]), '')

    def test_html_parser_and_hooks_preserve_context(self):
        data = {'results': [{'file': 'tests/XSpec.ts', 'suites': [
            {'title': 'first', 'tests': [{'title': 'same', 'fail': True}]},
            {'title': 'second', 'beforeHooks': [{'title': 'same', 'fail': True, 'isHook': True}]}]}]}
        rows = runner.extract(data)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]['context'], rows[1]['context'])
        self.assertTrue(rows[1]['hook'])
        parser = runner.RawReport()
        import json, html
        parser.feed('<body data-raw="' + html.escape(json.dumps(data), quote=True) + '">')
        self.assertEqual(parser.data, data)

    def test_uncertain_submission_never_posts(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runner, 'ROOT', Path(folder)):
            app = runner.App()
            app.get = lambda url: {'builds': []} if '/job/' in url else {'items': []}
            app.request = lambda *args: self.fail('Must not POST again')
            with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                app.validation('2026-10-19', {'submissionStarted': 'earlier'}, '**/XSpec.ts')

    def test_known_new_failure_keeps_tracker(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runner, 'ROOT', Path(folder)):
            app = runner.App()
            row = {'spec': 'DynamicComponentsV2_3', 'suite': 'components.DynamicComponentsV2_3', 'test': 'new', 'error': ''}
            self.assertEqual(app.classify(row)['tracker'], 'VBS-54227')
            row['spec'] = 'ManageExtensionLifecycle'
            self.assertEqual(app.classify(row)['tracker'], 'Locally passed')

    def test_ages_are_idempotent_per_source_build(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runner, 'ROOT', Path(folder)):
            app = runner.App(dry=True)
            row = {'spec': 'X', 'suite': 'X', 'test': 'a', 'tracker': 'Untracked'}
            source = {'number': 290, 'timestamp': 1792450000000}
            app.age([row], source)
            app.age([row], source)
            self.assertEqual(row['ageDays'], 1)
            app.age([row], {**source, 'number': 291})
            self.assertEqual(row['ageDays'], 2)

if __name__ == '__main__':
    unittest.main()
