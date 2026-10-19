#!/usr/bin/env python3
"""One-time migration; preserve source/validation/delivery IDs without resubmission."""
import json
import os
from pathlib import Path

root = Path(os.environ.get('SOLAR_REPORT_ROOT', Path(__file__).resolve().parent.parent))
state = root / 'state'
source = json.loads((state / 'imports/desktop-validation-state.json').read_text())
target = state / 'daily-state.json'
if not target.exists():
    records = {}
    for day, row in source.get('validations', {}).items():
        number = row.get('buildNumber') or row.get('build')
        if isinstance(number, dict):
            number = number.get('number')
        records[day] = {'source': row.get('sourceBuild'), 'validation': number,
                        'queueUrl': row.get('queueUrl'), 'filter': row.get('filter', ''),
                        'emailSent': bool(row.get('consolidatedEmailSent')),
                        'imported': True,
                        'desktopEmailMessageId': row.get('emailSentMessageId') or row.get('emailMessageId')}
        if row.get('validationSubmitted') or row.get('submissionAccepted') or row.get('submissionStartedAt'):
            records[day]['submissionStarted'] = row.get('submissionStartedAt') or 'imported accepted validation'
    target.write_text(json.dumps(records, indent=2))
history = state / 'failure-history.json'
if not history.exists():
    history.write_text((state / 'imports/desktop-failure-history.json').read_text())
print('State imported; existing active state was preserved. No builds or emails submitted.')
