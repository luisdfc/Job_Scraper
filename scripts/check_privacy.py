"""Reject local data in Git's index. Also usable by a pre-commit hook and CI.

Checks paths and obvious secret/profile markers, without printing file contents.
This guard complements .gitignore; neither can undo an earlier remote publication.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import PurePosixPath

PRIVATE_DIRS = {'private', 'runs', 'archive', '.venv', '__pycache__', '.pytest_cache'}
PRIVATE_FILES = {'config.json', 'scoring_profile.json', 'candidate_profile.md', 'resume.md', 'resume.txt',
                 'index.html', 'ranking.json', 'candidaturas.md'}


def private_path(name: str) -> bool:
    path = PurePosixPath(name.lower())
    if any(p in PRIVATE_DIRS or p.startswith(('output_backup_', 'job_scraper_actualizacion')) for p in path.parts):
        return True
    if path.parts[0] == 'output' and path.name != '.gitkeep':
        return True
    if path.name in PRIVATE_FILES or path.name.startswith(('profile_master_', 'fuentes_organizadas', 'candidaturas_', 'shortlist.')):
        return True
    if path.name.startswith('.env') and path.name != '.env.example':
        return True
    return bool(path.suffix in ('.sqlite', '.sqlite3', '.db', '.docx', '.pdf', '.xlsx', '.zip') or
                re.search(r'\.local\.(?:json|md)$|\.before_', path.name))


def main() -> int:
    names = subprocess.check_output(['git','ls-files','-z']).decode('utf-8').split('\0')
    bad = []
    for name in filter(None, names):
        if private_path(name):
            bad.append(name)
            continue
        # Inspect staged bytes: an unstaged edit must not conceal a staged secret.
        result = subprocess.run(['git','show',':'+name],capture_output=True)
        if result.returncode:
            continue
        content = result.stdout.decode('utf-8',errors='ignore')
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bsk-proj-[A-Za-z0-9_-]{40,}',content):
            bad.append(name)
        if re.search(r'^# (?:Master professional profile for|Perfil (?:maestro|profesional) de) ', content, re.M):
            bad.append(name)
    if bad:
        print('PRIVACY CHECK FAILED. Remove these local/sensitive files from the index:')
        for name in sorted(set(bad)):
            print(' - '+name)
        return 1
    print('Privacy check passed: no local profiles, results, databases or detected secrets in the Git index.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
