#!/usr/bin/env python3
"""Collect tokens privately; store them outside the Git source checkout."""
import argparse
import getpass
import json
import os
from pathlib import Path
import tempfile

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', action='store_true', help='Edit username and email settings')
    parser.add_argument('--smtp', action='store_true', help='Configure SMTP and optional authentication')
    parser.add_argument('--slack', action='store_true', help='Store optional Slack token; delivery stays disabled')
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(os.environ.get('SOLAR_REPORT_ROOT', Path(__file__).resolve().parent.parent))
    path = root / 'config/config.json'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    config = json.loads(path.read_text()) if path.exists() else {}
    def setting(key, label, default=''):
        old = str(config.get(key, default))
        value = input(label + (' [' + old + ']' if old else '') + ': ').strip() or old
        if not value or '\n' in value or '\r' in value:
            raise SystemExit('A valid value is required for ' + label)
        config[key] = value
    for key, label in [('jenkins_user', 'Jenkins username'), ('email_to', 'Report recipient email'), ('email_from', 'Report sender email')]:
        if args.settings or not config.get(key):
            setting(key, label, config.get('email_to', '') if key == 'email_from' else '')
    config.setdefault('smtp_host', 'localhost')
    config.setdefault('smtp_port', 25)
    config.setdefault('smtp_starttls', False)
    if args.smtp:
        setting('smtp_host', 'SMTP host', 'localhost')
        setting('smtp_port', 'SMTP port', '25')
        config['smtp_port'] = int(config['smtp_port'])
        config['smtp_starttls'] = input('Use STARTTLS? [y/N]: ').strip().lower() == 'y'
        config['smtp_user'] = input('SMTP username (blank for no login): ').strip()
        if config['smtp_user']:
            value = getpass.getpass('SMTP password (Enter keeps existing): ')
            if value:
                config['smtp_password'] = value
            if not config.get('smtp_password'):
                raise SystemExit('SMTP password is required')
        else:
            config.pop('smtp_password', None)
    fields = [('jenkins_token', 'Jenkins API token'), ('jira_token', 'Jira API token')]
    if args.slack:
        fields.append(('slack_token', 'Optional Slack OAuth token'))
    for key, label in fields:
        value = getpass.getpass(label + ' (Enter keeps existing): ')
        if value:
            config[key] = value
        if key != 'slack_token' and not config.get(key):
            raise SystemExit(label + ' is required; configuration not changed')
    config.update(delivery_mode='email', slack_enabled=False)
    # Secret values are saved only here; never printed or embedded in scripts.
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.config-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(config, stream, indent=2)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('Protected configuration saved at ' + str(path))
    print('Email-only delivery. Optional Slack token storage does not enable posting.')

if __name__ == '__main__':
    main()
