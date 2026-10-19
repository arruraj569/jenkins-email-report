#!/usr/bin/env python3
"""Read-only VM readiness checks. Does not submit builds or deliver messages."""
import base64
import json
import os
import smtplib
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

path = Path(os.environ.get('SOLAR_REPORT_ROOT', Path(__file__).resolve().parent.parent)) / 'config/config.json'
cfg = json.loads(path.read_text()) if path.exists() else {}
for name in ('http_proxy', 'https_proxy'):
    os.environ.setdefault(name, cfg.get(name, 'http://www-proxy-idc.in.oracle.com:80'))
errors = []
if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
    errors.append('Configuration permissions must be 600.')

def check(label, url, headers=None, validator=None):
    try:
        req = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(req, timeout=12) as response:
            payload = json.load(response)
        if validator and not validator(payload):
            raise ValueError('Authentication was not confirmed')
        print(label + ': OK')
    except urllib.error.HTTPError as exc:
        errors.append(label + ': HTTP ' + str(exc.code))
    except Exception as exc:
        errors.append(label + ': ' + type(exc).__name__)

if cfg.get('jenkins_token'):
    encoded = base64.b64encode((cfg.get('jenkins_user', 'aravind.kamaley@oracle.com')
                               + ':' + cfg['jenkins_token']).encode()).decode()
    check('Jenkins authentication',
          'https://ci-cloud.us.oracle.com/jenkins/vb-qa/whoAmI/api/json',
          {'Authorization': 'Basic ' + encoded},
          validator=lambda payload: payload.get('authenticated') is True)
else:
    errors.append('Jenkins token: missing')
if cfg.get('jira_token'):
    check('Jira authentication', 'https://jira.oraclecorp.com/jira/rest/api/2/myself',
          {'Authorization': 'Bearer ' + cfg['jira_token']},
          validator=lambda payload: bool(payload.get('name') or payload.get('accountId')))
else:
    errors.append('Jira token: missing')
try:
    with smtplib.SMTP(cfg.get('smtp_host', 'localhost'),
                      int(cfg.get('smtp_port', 25)), timeout=10) as client:
        client.ehlo()
    print('SMTP connection: OK (no email sent; delivery not yet verified)')
except Exception as exc:
    errors.append('SMTP connection: ' + type(exc).__name__)
for error in errors:
    print('BLOCKED: ' + error)
sys.exit(1 if errors else 0)
