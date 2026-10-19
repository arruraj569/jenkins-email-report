#!/usr/bin/env bash
set -eu
umask 077
source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
destination="${1:?Usage: ./install.sh /absolute/path/to/solar-report}"
python3 - "$source_dir" "$destination" <<'PY'
import os
from pathlib import Path
import shutil
import sys
import tempfile
if sys.version_info < (3, 9):
    raise SystemExit('Python 3.9 or newer is required')
source, root = map(Path, sys.argv[1:])
if not root.is_absolute() or any(c in str(root) for c in '\r\n%'):
    raise SystemExit('Use an absolute install path without percent signs or newlines')
root = root.resolve()
if root == source.resolve():
    raise SystemExit('Install outside the Git checkout to separate runtime files')
root.mkdir(parents=True, exist_ok=True, mode=0o700)
root.chmod(0o700)
for name in ['app', 'config', 'state', 'state/imports', 'reports', 'logs']:
    (root/name).mkdir(parents=True, exist_ok=True, mode=0o700)
    (root/name).chmod(0o700)
if (root/'config/config.json').exists():
    (root/'config/config.json').chmod(0o600)
for path in (source/'app').iterdir():
    if not path.is_file() or path.suffix not in ('.py', '.sh'):
        continue
    fd, temporary = tempfile.mkstemp(dir=str(root/'app'), prefix='.install-')
    os.close(fd)
    shutil.copyfile(path, temporary)
    os.chmod(temporary, 0o700 if path.suffix == '.sh' else 0o600)
    os.replace(temporary, root/'app'/path.name)
shutil.copyfile(source/'README.md', root/'README.md')
print('Installed application at ' + str(root))
print('Existing credentials, state, reports, and schedules were preserved.')
print('Configure: python3 ' + str(root/'app/configure.py') + ' --settings --smtp')
print('Check:     python3 ' + str(root/'app/preflight.py'))
print('Schedule:  python3 ' + str(root/'app/schedule.py') + ' enable')
PY
