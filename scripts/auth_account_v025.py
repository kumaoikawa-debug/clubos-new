"""Provision pre-verified identities. Production member identities require external verification.
Never pass passwords as a command-line argument (process list exposure).
"""
import argparse, getpass, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from db import init_db, conn
from security_v025 import install_tables, hash_password

parser=argparse.ArgumentParser()
parser.add_argument('command',choices=['create','disable'])
parser.add_argument('--username',required=True)
parser.add_argument('--role',choices=['platform','club','leader','member'])
parser.add_argument('--club-id',type=int)
parser.add_argument('--user-id',type=int)
args=parser.parse_args()
init_db(); install_tables()
username=args.username.strip().lower()
if args.command=='create':
    if not args.role: parser.error('--role required')
    if args.role in ('club','leader') and not args.club_id: parser.error('--club-id required')
    if args.role in ('member','leader') and not args.user_id: parser.error('--user-id required')
    if args.role=='platform' and (args.club_id or args.user_id): parser.error('platform cannot be tenant-bound')
    passwd=getpass.getpass('New password (14+ characters): ')
    if passwd != getpass.getpass('Repeat password: '): parser.error('passwords did not match')
    with conn() as c:
        if args.club_id and not c.execute('SELECT 1 FROM clubs WHERE id=?',(args.club_id,)).fetchone(): parser.error('unknown club')
        if args.user_id and not c.execute('SELECT 1 FROM users WHERE id=?',(args.user_id,)).fetchone(): parser.error('unknown user')
        c.execute('INSERT INTO auth_accounts(username,password_hash,role,club_id,user_id) VALUES(?,?,?,?,?)',(username,hash_password(passwd),args.role,args.club_id,args.user_id))
    print('Account created:',username,args.role)
else:
    with conn() as c:
        c.execute("UPDATE auth_accounts SET status='disabled' WHERE username=?",(username,))
        c.execute('UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE account_id IN (SELECT id FROM auth_accounts WHERE username=?)',(username,))
    print('Account disabled and sessions revoked:', username)
