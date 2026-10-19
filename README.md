# QA-MT-SOLAR VM reporter

Standalone, email-only QA-MT-SOLAR reporting for an always-on Linux VM. No
ChatGPT app, OpenAI API key, Node.js, or third-party Python packages are needed.
The runner uses Python's standard library, Jenkins/Jira APIs, and SMTP.

## Requirements

- Python 3.9+ and Bash; user crontab access and a running cron/crond service.
- Approved connectivity and valid corporate CA trust for the Oracle Jenkins and
  Jira endpoints defined near the top of `app/runner.py`.
- A working SMTP relay. A successful TCP connection alone does not prove email
  delivery; verify the first actual report in the recipient's mailbox.
- A writable installation location. Examples use `/scratch/$USER/solar-report`;
  use another absolute path if your VM has no `/scratch` directory.

This project targets the existing Oracle QA-MT-SOLAR workflow. Installing on
another VM does not remove its corporate-network or credential requirements.

## Install

Clone this source repository, then run from its root:

```bash
bash install.sh "/scratch/$USER/solar-report"
python3 "/scratch/$USER/solar-report/app/configure.py" --settings --smtp
python3 "/scratch/$USER/solar-report/app/preflight.py"
bash "/scratch/$USER/solar-report/app/run-report.sh" --dry-run
```

The installer copies application code and creates private runtime directories.
It preserves existing credentials/history/delivery state and does not enable
cron. The configuration prompt hides token/password input. The preview does not
submit Jenkins builds or send email. If no completed validation exists yet, it
reports that production would submit the Untracked filter, rather than inventing
a completed consolidated report.

After checking credentials, SMTP, preview output, and migrated state, enable:

```bash
python3 "/scratch/$USER/solar-report/app/schedule.py" enable
python3 "/scratch/$USER/solar-report/app/schedule.py" status
```

Only one machine should own the daily workflow. Pause the previous scheduler
before enabling a replacement. The scheduler checks every 15 minutes; the runner
applies 7 AM preparation and 10 AM delivery in Asia/Kolkata regardless of the VM
timezone. A pending validation defers email. Reinstalling code preserves cron.

## Tokens: storage and code

`app/configure.py` is the code that stores these configuration keys:

| Key | Usage |
| --- | --- |
| `jenkins_user`, `jenkins_token` | Jenkins HTTP Basic authentication |
| `jira_token` | Jira Bearer authentication for read-only issue lookups |
| `smtp_user`, `smtp_password` | Optional SMTP login |
| `slack_token` | Optional storage only; Slack posting is not implemented/enabled |

The real values are stored in `<install-root>/config/config.json`, with file
permissions **600** and a private **700** directory. This is protected plaintext,
not encryption. Restrict the VM account and disk access appropriately. Scripts
never print the values. `config.example.json` contains empty token fields and
documents the schema; it is not a real credentials file.

To collect an optional Slack token securely:

```bash
python3 "/scratch/$USER/solar-report/app/configure.py" --slack
```

This retains email-only operation. There is no Slack section in the email, no
Slack upload implementation, and no automated Slack posting.

Change SMTP/proxy settings in the protected runtime configuration as needed.
Set `http_proxy` and `https_proxy` to empty strings for an approved direct route.
Do not copy a personal shell profile into the application or disable TLS checks.

## Runtime files and migration

```text
<install-root>/
  app/                      application scripts
  config/config.json        private tokens and email settings
  state/daily-state.json    source/validation IDs and delivery records
  state/failure-history.json
  state/classifications.json       optional imported classification baseline
  state/classification-source.json optional {"sourceBuild": NUMBER} for its ages
  reports/                  HTML, plaintext body, sanitized extracted rows
  logs/runner.log            scheduler output
```

These runtime files are intentionally excluded from Git and the downloadable
source ZIP. Transfer state securely when migrating an existing installation,
after pausing the old owner and before enabling the new scheduler. Retain
`daily-state.json`, `failure-history.json`, and any classification baseline with
its source-build metadata. This prevents duplicate validations/emails and age
resets. An empty new installation starts a new observed baseline; it cannot
reconstruct historical ages automatically.

For the earlier desktop state schema, place the private original snapshots at
`state/imports/desktop-validation-state.json` and
`state/imports/desktop-failure-history.json`, then run `python3 app/import-state.py`
using the installed script's absolute path. It preserves existing active state.
Review migrated paths and IDs before activation. Snapshot files are not supplied.

## Behavior and limits

- Selects the latest completed timer-started QA-MT-SOLAR source build.
- Copies QA-MT-SOLAR-TEST **#162** parameters and changes only `testFilter` and
  `branch=origin/master`; validates only Untracked/Untracked — new specs.
- Uses one automatic validation per IST date, a process lock, stored queue IDs,
  and queue/build reconciliation. Uncertain submission outcomes stop for review.
- Waits for a completed usable merged rerun, keeps skipped/missing/ambiguous
  tests unresolved, and removes only exact confirmed validation passes.
- Preserves failure ages as verified source-run appearances, Jira mappings,
  current Jira assignees, full/Untracked filters, and a passed-test audit.
- Emails the HTML attachment and a concise report summary. **No Slack-copy
  content is included.** Jira is read-only; no ticket edits or creation.
- Missing merged-rerun evidence blocks delivery; there is no JUnit-only fallback.
  Runtime failures are logged; automatic failure-alert emails are not implemented.
- An interrupted SMTP submission remains uncertain for operator reconciliation;
  do not reset the state and resend without checking the Message-ID/mail queue.
- The explicit `--send-today` command supports one user-requested additional
  email per day; it is not the cron command and should not be used routinely.

The first VM report was tested against source #289 and validation #163. This is
not an end-to-end test of a new Jenkins validation submission on every possible
VM. Validate network access and delivery after deploying to each environment.

## Verify, update, and stop

```bash
tail -n 40 "/scratch/$USER/solar-report/logs/runner.log"
python3 "/scratch/$USER/solar-report/app/schedule.py" status
python3 "/scratch/$USER/solar-report/app/schedule.py" disable
```

To update: pull the reviewed source change, rerun `install.sh` against the same
installation root, and run the preview. Protect runtime state and configuration;
do not replace them with source examples.

## Add a new integration

The current application collects Jenkins/Jira data and delivers email. New
destinations require code changes; adding a token or setting a configuration
flag alone does not implement an integration.

### Where to change the code

| File | Integration changes |
| --- | --- |
| `app/runner.py` | `App` contains API access, report processing and delivery; `render()` builds the HTML and email body |
| `app/configure.py` | Add hidden credential prompts and non-secret settings; preserve existing values and private file permissions |
| `config.example.json` | Document new settings using empty credentials and disabled defaults |
| `app/preflight.py` | Validate required settings and connectivity only when the integration is enabled |
| `tests/` | Cover disabled mode, dry-run, success, API errors and duplicate/uncertain delivery |
| `install.sh` | Already copies top-level `app/*.py` and `app/*.sh`; extend it if adding subdirectories, templates or dependencies |

For a new service, add a separate top-level module such as `app/slack_client.py`
and call it explicitly from the runner. `App.request()` accepts only Jenkins/Jira
destinations and selects their credentials; use a dedicated client for any new
service so credentials are sent only to the intended service. Keep TLS validation
and timeouts enabled; avoid logging secrets, authorization headers or upload URLs.

Use an explicit enable setting, defaulting to false. Keep credentials in the
installed private `config/config.json` outside the checkout, and extend the
configuration helper rather than placing tokens in source code or command lines.
Record delivery status separately for each destination in runtime state. A Slack
failure must not cause an already-sent email to be repeated. Persist external
message/file IDs; reconcile uncertain sends before retrying. Honor rate limits,
and make `--dry-run` suppress every new outbound write.

### Optional Slack integration

**Implemented today: optional private token storage only.** Slack messages and
file uploads still need the client, runner wiring, configuration and tests above.
The current configuration helper explicitly sets `slack_enabled` to false; an
implementation must update that behavior before offering an enable option.

1. Create a workspace-approved Slack app. Under **OAuth & Permissions**, add Bot
   Token Scopes `chat:write` for messages and `files:write` if uploading reports.
   Install/reinstall the app and obtain its **Bot User OAuth Token**. Workspace
   approval may be required. App Configuration Tokens are not messaging tokens.
2. Invite the bot to the destination channel and record its channel ID. Add a
   non-secret `slack_channel_id` setting to the example and configuration helper.
3. Store the token privately on the installed VM:

   ```bash
   python3 "/scratch/$USER/solar-report/app/configure.py" --slack
   ```

   Enter keeps existing Jenkins/Jira credentials. The optional Slack token stays
   in `<install-root>/config/config.json` (file 600, directory 700), outside Git.
4. Implement messages with [`chat.postMessage`](https://docs.slack.dev/reference/methods/chat.postMessage/).
   Check both HTTP status and the JSON `ok` field before recording success.
5. For an HTML report upload, follow Slack's current
   [file upload flow](https://docs.slack.dev/messaging/working-with-files/):
   `files.getUploadURLExternal`, upload bytes to the returned URL, then
   `files.completeUploadExternal` with the destination `channel_id`. Use
   `files:write`; do not build a new integration using legacy `files.upload`.
6. Verify the VM can reach Slack API and upload hosts through its approved
   network route. Test in a chosen channel before enabling scheduled delivery.

Slack delivery is separate from the email renderer. Keep Slack-specific text out
of the email body. Request only the scopes required by the implemented features.

### Verify and deploy an integration change

Create a branch, implement and test the change, then reinstall reviewed source:

```bash
git switch -c codex/VB-add-slack-integration
python3 -m unittest discover -s tests -v
bash -n install.sh app/run-report.sh
bash install.sh "/scratch/$USER/solar-report"
python3 "/scratch/$USER/solar-report/app/preflight.py"
bash "/scratch/$USER/solar-report/app/run-report.sh" --dry-run
```

The branch command is an example for Slack; choose a descriptive name for other
services. Test delivery explicitly to a test destination after the preview.
Retain delivery history when updating or reverting code. Document configuration,
permissions, network requirements, failure handling and rollback in this README.
If adding Jira updates, implement them explicitly with separate permissions,
preview output and duplicate-write protection; today's Jira integration is read-only.

## Store in Git

Commit this **source directory**, not the installed `/scratch/...` directory:

```bash
git init
git branch -M main
git add .gitignore README.md config.example.json install.sh app tests
git commit -m "Add portable email-only QA-MT-SOLAR VM reporter"
git remote add origin YOUR_PRIVATE_REPOSITORY_URL
git push -u origin HEAD
```

Use a company-approved private repository for this corporate workflow. Never
commit actual tokens, runtime configuration, private keys, or state exports.
The `.gitignore` excludes those runtime locations as defense against mistakes.

### Create a private GitHub repository from your Mac

Install [GitHub CLI](https://cli.github.com/) first if `gh` is unavailable
(`brew install gh` if you already use Homebrew), then authenticate and publish:

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git --hostname github.com
cd /Users/akamaley/Documents/Jenkins/qa-mt-solar-vm
git init
git branch -M main
git add .gitignore README.md config.example.json install.sh app tests
git diff --cached --stat
git commit -m "Add portable QA-MT-SOLAR reporter and integration guide"
gh repo create qa-mt-solar-vm --private --source=. --remote=origin --push
gh repo view --web
```

Run these from the source folder shown, not its parent Jenkins workspace.
The repository is created in your authenticated GitHub account; use
`YOUR_ORG/qa-mt-solar-vm` instead of `qa-mt-solar-vm` to target an approved
organization where you have repository-creation access. If your company uses a
GitHub Enterprise host, authenticate against that host and use its repository URL.
For an already-created empty repository, use `git remote add origin URL` and
`git push -u origin main` instead of `gh repo create`.

See GitHub's [`gh repo create` documentation](https://cli.github.com/manual/gh_repo_create).

## Offline checks

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh app/run-report.sh
```
