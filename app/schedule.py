#!/usr/bin/env python3
"""Manage one marked cron entry while preserving unrelated schedules."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['enable', 'disable', 'status'])
    args = parser.parse_args()
    os.umask(0o077)
    root = Path(os.environ.get('SOLAR_REPORT_ROOT', Path(__file__).resolve().parent.parent)).resolve()
    if any(char in str(root) for char in '\r\n%'):
        raise SystemExit('Installation path cannot contain newlines or percent signs')
    old = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
    if old.returncode and 'no crontab' not in old.stderr.lower():
        raise SystemExit('Cannot read crontab; existing schedules were not changed')
    if args.action == 'status':
        print(old.stdout or 'No user crontab installed.')
        return
    if args.action == 'enable':
        subprocess.run([sys.executable, str(root / 'app/preflight.py')], check=True)
    (root / 'state').mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    (root / 'state' / ('crontab-before-' + stamp + '.txt')).write_text(old.stdout)
    existing = re.sub(r'(?ms)^# BEGIN SOLAR_REPORT_EMAIL\n.*?^# END SOLAR_REPORT_EMAIL\n?', '', old.stdout)
    result = existing.rstrip() + ('\n' if existing.strip() else '')
    if args.action == 'enable':
        command = ' '.join(shlex.quote(part) for part in [sys.executable, str(root / 'app/runner.py')])
        log = shlex.quote(str(root / 'logs/runner.log'))
        (root / 'logs').mkdir(mode=0o700, parents=True, exist_ok=True)
        result += ('# BEGIN SOLAR_REPORT_EMAIL\n'
                   '# Runner enforces 7 AM preparation and 10 AM delivery in Asia/Kolkata.\n'
                   '*/15 * * * * ' + command + ' >> ' + log + ' 2>&1\n'
                   '# END SOLAR_REPORT_EMAIL\n')
    subprocess.run(['crontab', '-'], input=result, text=True, check=True)
    saved = subprocess.check_output(['crontab', '-l'], text=True)
    if saved.count('# BEGIN SOLAR_REPORT_EMAIL') != (1 if args.action == 'enable' else 0):
        raise SystemExit('Crontab verification failed; inspect it before running manually')
    print('Solar schedule ' + ('enabled.' if args.action == 'enable' else 'disabled.'))
    print(saved)

if __name__ == '__main__':
    main()
