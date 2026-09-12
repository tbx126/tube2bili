"""Create local deployment configuration once, without printing credentials."""
from pathlib import Path
import os
import secrets

root = Path(__file__).resolve().parent.parent
target = root / '.env'
if target.exists():
    print('.env already exists; left unchanged.')
else:
    target.write_text('# Generated locally. Keep this file private.\nDASHBOARD_PASSWORD=' + secrets.token_urlsafe(24) + '\nPORT=8080\nBIND_ADDRESS=0.0.0.0\nMEDIA_ROOT=./data\nCOOKIE_SECURE=0\n', 'utf-8')
    os.chmod(target, 0o600)
    print('Created .env. Open it locally to read your dashboard password.')
