import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]

class InstallTests(unittest.TestCase):
    def test_reinstall_preserves_private_config_and_state(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'installed report'
            command = ['bash', str(SOURCE/'install.sh'), str(target)]
            subprocess.run(command, check=True, capture_output=True)
            (target/'config/config.json').write_text('private-test-placeholder')
            (target/'state/daily-state.json').write_text('{"preserve": true}')
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual((target/'config/config.json').read_text(), 'private-test-placeholder')
            self.assertEqual(json.loads((target/'state/daily-state.json').read_text()), {'preserve': True})
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            self.assertIn('dirname', (target/'app/run-report.sh').read_text())

    def test_credentials_are_private_and_never_printed(self):
        spec = importlib.util.spec_from_file_location('configure', SOURCE/'app/configure.py')
        configure = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(configure)
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with patch.dict(os.environ, {'SOLAR_REPORT_ROOT': directory}), \
                 patch('sys.argv', ['configure.py', '--slack']), \
                 patch('builtins.input', side_effect=['user@example.com', 'user@example.com', 'user@example.com']), \
                 patch('getpass.getpass', side_effect=['test-jenkins-secret', 'test-jira-secret', 'test-slack-secret']), \
                 patch('sys.stdout', output):
                configure.main()
            path = Path(directory)/'config/config.json'
            saved = json.loads(path.read_text())
            self.assertEqual(saved['slack_token'], 'test-slack-secret')
            self.assertFalse(saved['slack_enabled'])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            for token in ['test-jenkins-secret', 'test-jira-secret', 'test-slack-secret']:
                self.assertNotIn(token, output.getvalue())

if __name__ == '__main__':
    unittest.main()
