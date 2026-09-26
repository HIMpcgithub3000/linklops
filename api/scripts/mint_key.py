"""Mint an API key for local development and testing.

Usage:
    .venv/bin/python scripts/mint_key.py [tenant_name]
"""

import sys
from pathlib import Path
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.auth import mint

def main():
    tenant_name = sys.argv[1] if len(sys.argv) > 1 else "m04-tenant-a"
    key_id, secret, secret_hash = mint()
    full_api_key = f"{key_id}.{secret}"

    engine = create_engine(str(settings.MIGRATION_DATABASE_URL))
    with engine.begin() as conn:
        tenant_id = conn.execute(
            text("SELECT id FROM tenants WHERE name = :name"),
            {"name": tenant_name}
        ).scalar()
        if not tenant_id:
            print(f"Error: Tenant '{tenant_name}' not found.")
            return 1

        conn.execute(
            text("INSERT INTO api_keys (key_id, tenant_id, secret_hash, label) VALUES (:kid, :tid, :sh, 'dev-key')"),
            {"kid": key_id, "tid": tenant_id, "sh": secret_hash}
        )

    print(f"API Key successfully minted for tenant '{tenant_name}':")
    print(f"\n  {full_api_key}\n")
    print("Use this header in your requests:")
    print(f'  -H "X-API-Key: {full_api_key}"')
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
