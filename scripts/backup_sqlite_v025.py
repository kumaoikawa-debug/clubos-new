"""Consistent SQLite backup for the current ClubOS instance.

A backup is not a disaster-recovery guarantee: restore rehearsal, offsite encryption,
and a transaction/RPO policy must be supplied by the deployment operator.
"""
import argparse, hashlib, os, sqlite3, tempfile
from pathlib import Path
from datetime import datetime, timezone
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from db import DB_PATH

parser=argparse.ArgumentParser(description='Use SQLite online backup API (not raw copying a live DB)')
parser.add_argument('--source',default=str(DB_PATH))
parser.add_argument('--directory',required=True)
args=parser.parse_args()
source=Path(args.source).expanduser().resolve()
dest_dir=Path(args.directory).expanduser().resolve()
if not source.is_file():parser.error('source database does not exist')
if dest_dir==source.parent and source.name.startswith('clubos-backup'):
    parser.error('do not back up an existing backup to the same directory')
dest_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
if dest_dir.stat().st_mode & 0o077: parser.error('backup directory must not be readable by group/others (chmod 700)')
name='clubos-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite'
final=dest_dir/name
fd,temp=tempfile.mkstemp(prefix='.backup-',suffix='.sqlite',dir=dest_dir)
os.fchmod(fd,0o600);os.close(fd)
try:
    with sqlite3.connect(f'file:{source}?mode=ro',uri=True) as s,sqlite3.connect(temp) as t:
        s.backup(t)
    with sqlite3.connect(temp) as test:
        if test.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('backup integrity_check failed')
    os.replace(temp,final);os.chmod(final,0o600)
    print('Verified SQLite backup:',final)
    print('SHA256:',hashlib.sha256(final.read_bytes()).hexdigest())
finally:
    if os.path.exists(temp):os.unlink(temp)
