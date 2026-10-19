#!/usr/bin/env python3
"""Email-only Solar reporting daemon; standard library, persistent deduplication."""
import argparse
import base64
import copy
from collections import Counter
import fcntl
import html
from html.parser import HTMLParser
import http.cookiejar
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

ROOT = Path(os.environ.get('SOLAR_REPORT_ROOT', Path(__file__).resolve().parent.parent))
JENKINS = 'https://ci-cloud.us.oracle.com/jenkins/vb-qa/'
MAIN = JENKINS + 'job/QA-MT-SOLAR/'
TEST = JENKINS + 'job/QA-MT-SOLAR-TEST/'
JIRA = 'https://jira.oraclecorp.com/jira/'
IST = timezone(timedelta(hours=5, minutes=30))
NORM = lambda s: re.sub('[^a-z0-9]', '', str(s).lower())

def load(path, default):
    return json.loads(path.read_text()) if path.exists() else copy.deepcopy(default)

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    with open(temp, 'w', opener=lambda p, f: os.open(p, f, 0o600)) as stream:
        json.dump(data, stream, indent=2)
    os.replace(temp, path)

def clean(text):
    text = re.sub(r'(?i)((?:password|passwd|token|authorization|bypass.?code)\s*[:=]\s*)[^\s,;]+', r'\1[REDACTED]', str(text))
    text = re.sub(r'(https?://)[^/\s:@]+:[^@\s]+@', r'\1[REDACTED]@', text)
    return text[:1600]

def spec_filter(rows):
    names = sorted({r['spec'] for r in rows})
    return ('**/' + (names[0] if len(names) == 1 else '{' + ','.join(names) + '}') + 'Spec.ts') if names else ''

def key(row):
    return (NORM(row['spec']), NORM(re.sub(r'Spec$', '', row['suite'])), NORM(row['test']))

class RawReport(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.data = None
    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name == 'data-raw':
                self.data = json.loads(value)

def extract(report):
    rows = []
    def walk(node, inherited='', titles=()):
        file = node.get('file') or node.get('fullFile') or inherited
        title = node.get('title', '')
        context = titles + ((title,) if title else ())
        stem = re.sub(r'(?:Spec)?\.[tj]s$', '', file.replace('\\', '/').split('/')[-1])
        relative = re.split(r'/tests/', file.replace('\\', '/'))[-1]
        suite = re.sub(r'(?:Spec)?\.[tj]s$', '', relative).strip('./').replace('/', '.')
        for test in node.get('tests', []) + node.get('beforeHooks', []) + node.get('afterHooks', []):
            state = 'PASSED' if test.get('pass') else 'FAILED' if test.get('fail') else str(test.get('state') or 'NOT_EXECUTED').upper()
            if state == 'PENDING' or test.get('skipped'):
                state = 'SKIPPED'
            if not stem:
                if state == 'FAILED':
                    raise RuntimeError('Failed hook has no spec identity; report needs manual review')
                continue
            rows.append({'spec': stem, 'suite': suite, 'test': test.get('title', ''),
                         'context': ' / '.join(context), 'hook': bool(test.get('isHook')),
                         'state': state, 'error': clean((test.get('err') or {}).get('message', ''))})
        for child in node.get('suites', []):
            walk(child, file, context)
    for node in report['results']:
        walk(node)
    unique = {}
    for row in rows:
        unique[(key(row), row['context'])] = row
    return list(unique.values())

MAP = [
    ('SpecFlowMinimal|ConvertAdfDescribeService|SpecFlowOa3Dynamic', 'VBS-54226', 'Web Services', 'Roopesh Kumar'),
    ('DynamicComponentsV2_3', 'VBS-54227', 'Dynamic Components', 'Huiyu Chen'),
    ('BasicBOSSBusinessObject|HR_Application', 'VBS-54228', 'VBS Business Objects / PM Samples', 'Garima Jain'),
    ('WorkspacePerformance', 'VBS-54229', 'Performance', 'Krishnendu Nair'),
    ('SessionExpire|CCAMetadata', 'VBS-54230', 'User Access / CCA Components', 'Aravind Kamaley'),
    ('Oa3Service|AuthenticationMechanism|ServerVariablesService|OrdsService|FaLifecycle|StaticFaService|ServiceByEndpoint|SpecFlowStatic|SpecFlowDynamic', 'VBS-54137', 'Web Services', 'Roopesh Kumar'),
    ('AppExtensionBackends', 'VBS-53898', 'Application Extensions – Services', 'Akshaya Narasiman'),
    ('PWA', 'VBS-53947', 'PWA', 'Garima Jain'),
    ('DataPalette|VBSApplication|RecoveryModeImport', 'VBS-54287', 'Page Designer / Life Cycle / Recovery', 'Not listed'),
    ('CXCustomization', 'VBS-53168', 'Application Extensions – CX', 'Akshaya Narasiman')]

class App:
    def __init__(self, dry=False):
        self.dry = dry
        self.cfg = load(ROOT / 'config/config.json', {})
        for name in ('http_proxy', 'https_proxy'):
            os.environ.setdefault(name, self.cfg.get(name, 'http://www-proxy-idc.in.oracle.com:80'))
        self.state_path = ROOT / 'state/daily-state.json'
        self.states = load(self.state_path, {})
        self.history = load(ROOT / 'state/failure-history.json', {'failures': {}})
        self.baseline = load(ROOT / 'state/classifications.json', [])
        self.baseline_source = load(ROOT / 'state/classification-source.json', {}).get('sourceBuild')
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, url, form=None):
        if not url.startswith((JENKINS, JIRA)):
            raise RuntimeError('Unexpected API destination')
        token = self.cfg.get('jenkins_token' if url.startswith(JENKINS) else 'jira_token')
        if not token:
            raise RuntimeError('Required API credential is missing')
        auth = 'Bearer ' + token
        if url.startswith(JENKINS):
            auth = 'Basic ' + base64.b64encode((self.cfg['jenkins_user'] + ':' + token).encode()).decode()
        headers = {'Authorization': auth}
        if form is not None:
            # Keep the crumb and its cookie in the same HTTP session.
            try:
                crumb = self.get(JENKINS + 'crumbIssuer/api/json')
                headers[crumb['crumbRequestField']] = crumb['crumb']
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
        req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode() if form is not None else None, headers=headers)
        with self.opener.open(req, timeout=60) as response:
            return response.read(), response.headers.get('Location', '')

    def get(self, url):
        return json.loads(self.request(url)[0])

    def persist(self):
        if not self.dry:
            save(self.state_path, self.states)

    def metadata(self, base, number):
        return self.get(base + str(number) + '/api/json?tree=number,url,building,result,timestamp,duration,queueId,actions[parameters[name,value],causes[_class,shortDescription]]')

    def report(self, base, number):
        url = base + str(number) + '/artifact/rerun-artifacts/merged/rerun-report.html'
        parser = RawReport()
        parser.feed(self.request(url)[0].decode())
        if not parser.data or 'stats' not in parser.data:
            raise RuntimeError('Published merged rerun is missing or invalid; no final report will be sent')
        return parser.data, url

    def rerun_stage(self, base, number):
        try:
            stages = self.get(base + str(number) + '/wfapi/describe').get('stages', [])
            matches = [s for s in stages if re.search('rerun|re-run', s.get('name', ''), re.I)]
            return '; '.join(s['name'] + ': ' + s.get('status', 'unknown') for s in matches) or 'No separate rerun stage found'
        except urllib.error.HTTPError:
            return 'Stage API unavailable; execution not independently verified'

    def classify(self, row):
        row.update(tracker='Untracked', feature='Not listed', resource='Not listed')
        known = next((r for r in self.baseline if key(r) == key(row)), None)
        if known:
            for field in ('tracker', 'feature', 'resource'):
                row[field] = known.get(field, row[field])
        for pattern, tracker, feature, resource in MAP:
            if re.search(pattern, row['spec']):
                row.update(tracker=tracker, feature=feature, resource=resource)
                break
        if row['spec'] in ('MATSMultiTenant', 'PipelineSanity', 'UndoRedoService', 'ManageExtensionLifecycle'):
            row['tracker'] = 'Locally passed'
        if row['spec'].startswith('PrepareUpgradeServices'):
            row['tracker'] = 'Not related to release — setup required'
        if row['spec'] == 'OPA':
            row['tracker'] = 'Setup required — credentials not updated'
        error = row['error']
        if 'Timed out waiting for locator By(css selector, div[id="vb-droppane"] > div[data-row="0"][data-layout-internal-id=' in error or 'Timed out waiting for locator By(css selector, div[id="vb-droppane"] div[data-layout-internal-id' in error:
            row.update(tracker='VBS-54059', feature='Dynamic Components', resource='Huiyu Chen')
        return row

    def age(self, rows, source):
        cache = self.history.setdefault('failures', {})
        for row in rows:
            old_key = NORM(row['spec']) + '||' + NORM(row['test'])
            entry = cache.setdefault(old_key, {'firstSeen': datetime.fromtimestamp(source['timestamp']/1000, timezone.utc).isoformat(), 'ageRuns': 0})
            known = next((r for r in self.baseline if key(r) == key(row)), None)
            if known and self.baseline_source and known.get('ageDays', 0) > entry.get('ageRuns', 0):
                entry.update(ageRuns=known['ageDays'], firstSeen=known['firstSeen'], lastBuildUrl=MAIN + str(self.baseline_source) + '/')
            observed = entry.setdefault('observedBuilds', [])
            old_number = re.search(r'/(\d+)/?$', entry.get('lastBuildUrl', ''))
            last = int(old_number.group(1)) if old_number else None
            if source['number'] not in observed and last != source['number']:
                entry['ageRuns'] = int(entry.get('ageRuns', entry.get('ageDays', 0))) + 1
                observed.append(source['number'])
            entry.update(lastBuildUrl=MAIN + str(source['number']) + '/')
            row.update(firstSeen=entry['firstSeen'][:10], ageDays=entry.get('ageRuns') or 1)
            if row['tracker'].startswith('Untracked'):
                row['tracker'] = 'Untracked — new' if row['ageDays'] == 1 else 'Untracked'
        if not self.dry:
            save(ROOT / 'state/failure-history.json', self.history)

    def params(self, build):
        return {p['name']: p.get('value', '') for a in build.get('actions', []) for p in a.get('parameters', [])}

    def validation(self, day, state, filter_value):
        if not filter_value:
            state['validationUnnecessary'] = True
            return None
        number = state.get('validation')
        queue = state.get('queueUrl')
        if queue and not number:
            try:
                q = self.get(queue.rstrip('/') + '/api/json')
                if q.get('cancelled'):
                    raise RuntimeError('Validation queue was cancelled; automatic resubmission is disabled')
                number = (q.get('executable') or {}).get('number')
                if not number:
                    return None
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
        if not number:
            # Recover accepted/uncertain submissions before any POST.
            recent = self.get(TEST + 'api/json?tree=builds[number,timestamp,queueId,actions[parameters[name,value]]]{0,60}')
            candidates = [b for b in recent.get('builds', []) if datetime.fromtimestamp(b['timestamp']/1000, IST).date().isoformat() == day and self.params(b).get('testFilter') == filter_value and self.params(b).get('branch') == 'origin/master']
            queued = self.get(JENKINS + 'queue/api/json?tree=items[url,task[url],actions[parameters[name,value]]]')
            matches = [q for q in queued.get('items', []) if q.get('task', {}).get('url', '').rstrip('/').endswith('/QA-MT-SOLAR-TEST') and self.params(q).get('testFilter') == filter_value and self.params(q).get('branch') == 'origin/master']
            if len(candidates) > 1 or len(matches) > 1:
                raise RuntimeError('Multiple matching validations require manual reconciliation')
            if candidates:
                number = candidates[0]['number']
            elif matches:
                state['queueUrl'] = matches[0]['url']
                self.persist()
                return None
            elif state.get('submissionStarted'):
                raise RuntimeError('Prior submission outcome uncertain; no automatic retry')
            elif self.dry:
                raise RuntimeError('Dry run needs a completed validation; production would submit the Untracked filter')
            else:
                params = self.params(self.metadata(TEST, 162))
                if not {'testFilter', 'branch'} <= params.keys():
                    raise RuntimeError('Reference #162 parameters are unavailable')
                params.update(testFilter=filter_value, branch='origin/master')
                state['submissionStarted'] = datetime.now(timezone.utc).isoformat()
                self.persist()
                form = {k: str(v).lower() if isinstance(v, bool) else str(v) for k, v in params.items()}
                _, location = self.request(TEST + 'buildWithParameters', form)
                if not location.startswith(JENKINS + 'queue/item/'):
                    raise RuntimeError('Submission accepted without a verifiable queue URL; reconcile manually')
                state['queueUrl'] = location
                self.persist()
                return None
        state['validation'] = number
        self.persist()
        meta = self.metadata(TEST, number)
        if meta.get('building'):
            return None
        report, url = self.report(TEST, number)
        if not report['stats'].get('tests'):
            raise RuntimeError('Validation ended without usable tests; no retry or email')
        return meta, report, url

    def run(self, send_today=False):
        now = datetime.now(IST)
        if now.hour < 7 and not self.dry and not send_today:
            return
        day = now.date().isoformat()
        state = self.states.setdefault(day, {})
        if state.get('emailPhase') == 'SENDING':
            raise RuntimeError('Email outcome uncertain; inspect the saved Message-ID and SMTP queue before retrying')
        if state.get('emailSent') and not self.dry and not (send_today and not state.get('requestedEmailSent')):
            print('Today is already delivered; nothing submitted or sent.')
            return
        if not state.get('source'):
            job = self.get(MAIN + 'api/json?tree=builds[number,building,result,timestamp,actions[causes[_class,shortDescription]]]{0,100}')
            builds = [b for b in job['builds'] if not b.get('building') and any('TimerTriggerCause' in c.get('_class', '') for a in b.get('actions', []) for c in a.get('causes', []))]
            if not builds:
                raise RuntimeError('No completed timer-started Solar build found')
            state['source'] = max(builds, key=lambda b: b['number'])['number']
            if any(s.get('source') == state['source'] and s.get('emailSent') for d, s in self.states.items() if d != day):
                raise RuntimeError('Latest source was already delivered on another date; waiting for a new timer build')
            self.persist()
        source = self.metadata(MAIN, state['source'])
        source['rerunStage'] = self.rerun_stage(MAIN, state['source'])
        report, report_url = self.report(MAIN, state['source'])
        rows = [self.classify(r) for r in extract(report) if r['state'] == 'FAILED']
        self.age(rows, source)
        untracked = spec_filter([r for r in rows if r['tracker'].startswith('Untracked')])
        state.setdefault('filter', untracked)
        validation = self.validation(day, state, state['filter'])
        if state['filter'] and validation is None:
            print('Validation queued/running. Delivery deferred.')
            return
        audit = []
        if validation:
            validation[0]['rerunStage'] = self.rerun_stage(TEST, validation[0]['number'])
            outcomes = extract(validation[1])
            for row in rows[:]:
                matches = [r for r in outcomes if key(r) == key(row) and r['context'] == row['context']]
                # Ambiguous context or missing results can never erase a failure.
                row['validation'] = matches[0]['state'] if len(matches) == 1 else 'Not revalidated'
                if len(matches) == 1 and matches[0]['state'] == 'PASSED':
                    rows.remove(row)
                    audit.append(row)
            identities = {(key(r), r['context']) for r in rows + audit}
            for row in outcomes:
                if row['state'] == 'FAILED' and (key(row), row['context']) not in identities:
                    self.classify(row)
                    history_key = NORM(row['spec']) + '||' + NORM(row['test'])
                    old = self.history['failures'].get(history_key, {})
                    row.update(firstSeen=old.get('firstSeen', now.isoformat())[:10], ageDays=old.get('ageRuns', 1), validation='FAILED')
                    if row['tracker'] == 'Untracked':
                        row['tracker'] = 'Untracked — new' if not old else 'Untracked'
                    if not old:
                        self.history['failures'][history_key] = {'firstSeen': now.isoformat(), 'ageRuns': 1, 'lastBuildUrl': MAIN + str(source['number']) + '/', 'observedBuilds': [source['number']], 'firstObservedInValidation': validation[0]['number']}
                    rows.append(row)
        if not self.dry:
            save(ROOT / 'state/failure-history.json', self.history)
        for row in rows:
            row.setdefault('validation', 'Not revalidated')
            row['assignee'] = 'Not applicable'
        for tracker in {r['tracker'] for r in rows if re.fullmatch(r'VBS-\d+', r['tracker'])}:
            issue = self.get(JIRA + 'rest/api/2/issue/' + tracker + '?fields=assignee,status,summary')
            for row in rows:
                if row['tracker'] == tracker:
                    row['assignee'] = (issue['fields'].get('assignee') or {}).get('displayName', 'Unassigned')
                    row['jiraStatus'] = issue['fields']['status']['name']
        document, body = render(source, report, report_url, validation, rows, audit)
        output = ROOT / 'reports' / ('qa-mt-solar-' + str(source['number']) + '-consolidated-report.html')
        output.parent.mkdir(mode=0o700, exist_ok=True)
        output.write_text(document)
        output.with_suffix('.txt').write_text(body)
        save(output.with_suffix('.json'), {'rows': rows, 'audit': audit, 'source': source['number'], 'validation': state.get('validation')})
        print('Report generated:', str(output), 'active rows:', len(rows))
        if self.dry:
            print('Dry run: no build or email submitted; persistent state not changed.')
            return
        if now.hour < 10:
            print('Final report ready; waiting for 10 AM IST.')
            return
        msg = EmailMessage()
        msg['Subject'] = 'QA-MT-SOLAR #' + str(source['number']) + ': Jenkins report'
        msg['From'] = self.cfg.get('email_from') or self.cfg['email_to']
        msg['To'] = self.cfg['email_to']
        msg['Date'] = formatdate(localtime=False)
        msg['Message-ID'] = make_msgid(domain='oracle.com')
        msg.set_content(body)
        msg.add_attachment(document.encode(), maintype='text', subtype='html', filename=output.name)
        state.update(emailPhase='SENDING', emailMessageId=msg['Message-ID'])
        self.persist()
        # A crash or ambiguous SMTP outcome leaves SENDING for manual reconciliation.
        with smtplib.SMTP(self.cfg.get('smtp_host', 'localhost'), int(self.cfg.get('smtp_port', 25)), timeout=45) as client:
            client.ehlo()
            if self.cfg.get('smtp_starttls'):
                client.starttls(context=ssl.create_default_context())
            if self.cfg.get('smtp_user'):
                client.login(self.cfg['smtp_user'], self.cfg['smtp_password'])
            refused = client.send_message(msg)
            if refused:
                raise RuntimeError('SMTP rejected a report recipient')
        state.update(emailSent=True, emailPhase='SMTP_ACCEPTED', emailAcceptedAt=now.isoformat(), report=str(output))
        if send_today:
            state['requestedEmailSent'] = True
        self.persist()
        print('SMTP accepted report. Message-ID:', msg['Message-ID'])


def render(source, report, url, validation, rows, audit):
    e = lambda value: html.escape(str(value))
    stats = report['stats']
    full = spec_filter(rows)
    untracked = spec_filter([r for r in rows if r['tracker'].startswith('Untracked')])
    source_url = MAIN + str(source['number']) + '/'
    links = '<a href="' + source_url + '">Source build</a> · <a href="' + url + '">Source rerun</a>'
    validation_text = 'Validation not needed: no Untracked failures.'
    if validation:
        m, r, u = validation
        s = r['stats']
        validation_text = 'Validation #' + str(m['number']) + ': ' + m['result'] + '; merged rerun ' + str(s.get('passes', 0)) + ' passed / ' + str(s.get('failures', 0)) + ' failed / ' + str(s.get('pending', 0) + s.get('skipped', 0)) + ' skipped. Built-in rerun stage: ' + m.get('rerunStage', 'Not verified') + '.'
        links += ' · <a href="' + TEST + str(m['number']) + '/">Validation build</a> · <a href="' + u + '">Validation rerun</a>'
    body = ('Jenkins report: ' + source_url + '\n\nSource result: ' + source['result'] + '\nSource merged rerun: ' + str(stats.get('passes', 0)) + ' passed / ' + str(stats.get('failures', 0)) + ' failed / ' + str(stats.get('pending', 0) + stats.get('skipped', 0)) + ' skipped.\n' + validation_text + '\nActive failure rows: ' + str(len(rows)) + '; exact passed audit: ' + str(len(audit)) + '\n\nFull filter:\n' + (full or 'None') + '\n\nUntracked filter:\n' + (untracked or 'None') + '\n\nComplete HTML report attached. Failure ages are verified minimum appearances; historical missing evidence remains disclosed.\n')
    columns = ['Suite', 'Test', 'Error', 'Test filter', 'Tracker / Jira', 'Feature', 'Assignee', 'Resource', 'First seen', 'Age', 'Validation']
    table = ''
    for row in rows:
        tracker = row['tracker']
        tracker_html = '<a href="' + JIRA + 'browse/' + tracker + '">' + e(tracker) + '</a>' if re.fullmatch(r'VBS-\d+', tracker) else e(tracker)
        cells = [e(row['suite']), e(row['test']), e(row['error']), '<code>' + e(spec_filter([row])) + '</code>', tracker_html, e(row['feature']), e(row.get('assignee', 'Not applicable')), e(row['resource']), e(row['firstSeen']), e(str(row['ageDays']) + ' verified minimum appearances'), e(row['validation'])]
        table += '<tr>' + ''.join('<td>' + cell + '</td>' for cell in cells) + '</tr>'
    controls = '<input id="q" placeholder="Search failed rows" aria-label="Search failed rows"><select id="col" aria-label="Filter column"><option value="all">All columns</option>' + ''.join('<option value="' + str(i) + '">' + e(c) + '</option>' for i, c in enumerate(columns)) + '</select>'
    document = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>QA-MT-SOLAR #' + str(source['number']) + '</title><style>body{font:14px system-ui;color:#172b4d;background:#f3f6fb;margin:24px}header{background:#132d55;color:white;padding:26px;border-radius:12px}header a{color:white}.cards{display:flex;gap:15px;flex-wrap:wrap;margin:18px 0}.card,section{padding:18px;border-radius:10px;background:white;border:1px solid #d8e0eb;margin-bottom:16px}.card{flex:1;font-size:21px;color:#9b6100}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eaf1fb;padding:12px}table{border-collapse:collapse;width:100%;font-size:12px}th{background:#183c69;color:white;cursor:pointer}th,td{padding:10px;text-align:left;border-bottom:1px solid #dde4ed;vertical-align:top}tr:nth-child(even){background:#f1f5fa}input,select{padding:9px;margin:8px}.scroll{overflow:auto}td{max-width:360px;overflow-wrap:anywhere}</style><header><h1>QA-MT-SOLAR #' + str(source['number']) + ' — consolidated report</h1>' + links + '</header><div class="cards">' + ''.join('<div class="card">' + e(label) + '<br><strong>' + e(value) + '</strong></div>' for label, value in [('Result', source['result']), ('Active failure rows', len(rows)), ('Source skipped', stats.get('pending', 0) + stats.get('skipped', 0)), ('Duration', str(round(source['duration']/60000, 1)) + ' min')]) + '</div><section><p>' + e(validation_text) + '</p><p>Source merged rerun: ' + e(str(stats.get('passes', 0)) + ' passed / ' + str(stats.get('failures', 0)) + ' failed / ' + str(stats.get('pending', 0) + stats.get('skipped', 0)) + ' skipped') + '. These published totals are separate from extracted rows, which may include failed hooks.</p><p>Age counts verified daily-source appearances only. Historical missing report evidence (#238/#258/#287) remains unresolved; counts are verified minimums, not calendar days. A published rerun artifact is evidence of rerun results; this runner does not independently assert pipeline-stage execution.</p></section><section><h2>Full filter</h2><pre>' + e(full or 'None needed') + '</pre><h2>Untracked filter</h2><pre>' + e(untracked or 'None needed') + '</pre></section><section><h2>Active failed rows</h2>' + controls + '<div class="scroll"><table id="rows"><thead><tr>' + ''.join('<th tabindex="0">' + e(c) + '</th>' for c in columns) + '</tr></thead><tbody>' + table + '</tbody></table></div></section><section><h2>Passed in Solar Test audit</h2><ul>' + ''.join('<li>' + e(r['suite'] + ' | ' + r['test']) + '</li>' for r in audit) + '</ul><p>Locally passed is a user classification, not a confirmed Jenkins pass. Missing, skipped, or ambiguous validation results never remove a source failure.</p></section><script>const t=document.getElementById("rows"),q=document.getElementById("q"),c=document.getElementById("col");function filter(){for(const r of t.tBodies[0].rows){let s=c.value==="all"?r.textContent:r.cells[+c.value].textContent;r.hidden=!s.toLowerCase().includes(q.value.toLowerCase())}}q.oninput=c.onchange=filter;for(const [i,h] of [...t.tHead.rows[0].cells].entries()){let asc=true;function sort(){[...t.tBodies[0].rows].sort((a,b)=>a.cells[i].textContent.localeCompare(b.cells[i].textContent,undefined,{numeric:true})*(asc?1:-1)).forEach(r=>t.tBodies[0].append(r));asc=!asc}h.onclick=sort;h.onkeydown=e=>{if(e.key==="Enter")sort()}}</script></html>'
    return document, body

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--send-today', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    (ROOT / 'state').mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(ROOT / 'state/runner.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            App(args.dry_run).run(args.send_today)
        except urllib.error.HTTPError as exc:
            print('BLOCKED: API returned HTTP', exc.code)
            return 1
        except Exception as exc:
            print('BLOCKED:', clean(str(exc)) if isinstance(exc, RuntimeError) else type(exc).__name__)
            return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
