from __future__ import annotations

import shutil
from datetime import datetime

from app.services.sop_store import SOPStore


def main() -> None:
    store = SOPStore()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot_backup = store.snapshot_path.with_name(f"{store.snapshot_path.stem}.{ts}.bak{store.snapshot_path.suffix}")
    wal_backup = store.wal_path.with_name(f"{store.wal_path.stem}.{ts}.bak{store.wal_path.suffix}")

    if store.snapshot_path.exists():
        shutil.copy2(store.snapshot_path, snapshot_backup)
    if store.wal_path.exists():
        shutil.copy2(store.wal_path, wal_backup)

    summary = store.cleanup_historical_records()
    print(
        {
            "snapshot_backup": str(snapshot_backup) if store.snapshot_path.exists() else None,
            "wal_backup": str(wal_backup) if store.wal_path.exists() else None,
            **summary,
        }
    )


if __name__ == "__main__":
    main()
