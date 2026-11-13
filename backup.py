"""Backups: everything that can't be re-downloaded — the SQLite database (a
consistent online snapshot) plus shared files and chat images — in one .zip.

- Admin page /admin/backup: download a backup now; see automatic backups.
- Automatic: with BACKUP_DIR set (e.g. a USB drive), a backup is written every
  BACKUP_EVERY_HOURS and only the newest BACKUP_KEEP are kept.
- CLI: python backup.py create [DIR]      — write a backup (default BACKUP_DIR or ./backups)
       python backup.py restore FILE.zip  — restore (stop the server first; the
                                            current data is kept as *.before-restore)
Models aren't included: `python demo_setup.py --download-model` fetches them again.
"""
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import config

DATA_DIRS = {"files": "FILES_DIR", "images": "IMAGES_DIR"}


def _snapshot_db(dest):
    """Consistent copy of the live database (sqlite backup API, safe while running)."""
    src = sqlite3.connect(config.DB_PATH)
    try:
        out = sqlite3.connect(dest)
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()


def create(dest_dir=None):
    """Write towerai-backup-YYYYmmdd-HHMMSS.zip into dest_dir; returns its path."""
    dest_dir = Path(dest_dir or config.BACKUP_DIR or Path(config.BASE_DIR) / "backups")
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"towerai-backup-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "towerai.db"
        _snapshot_db(db_copy)
        partial = path.with_suffix(".zip.part")
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(db_copy, "towerai.db")
            for name, key in DATA_DIRS.items():
                root = Path(getattr(config, key))
                if root.is_dir():
                    for f in root.rglob("*"):
                        if f.is_file():
                            z.write(f, f"{name}/{f.relative_to(root)}")
        partial.rename(path)
    return path


def list_backups(dest_dir=None):
    d = Path(dest_dir or config.BACKUP_DIR or Path(config.BASE_DIR) / "backups")
    return sorted(d.glob("towerai-backup-*.zip"), reverse=True) if d.is_dir() else []


def prune(dest_dir=None, keep=None):
    keep = config.BACKUP_KEEP if keep is None else keep
    for old in list_backups(dest_dir)[keep:]:
        old.unlink(missing_ok=True)


def maybe_auto(now=None):
    """Called by the scheduler thread: back up to BACKUP_DIR when one is due."""
    if not config.BACKUP_DIR or config.BACKUP_EVERY_HOURS <= 0:
        return None
    now = now or time.time()
    latest = list_backups()
    if latest and now - latest[0].stat().st_mtime < config.BACKUP_EVERY_HOURS * 3600:
        return None
    path = create()
    prune()
    return path


def restore(zip_path):
    """Replace the database and data folders with a backup's contents."""
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if "towerai.db" not in names:
            raise ValueError("not a TowerAI backup (no towerai.db inside)")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        db_path = Path(config.DB_PATH)
        if db_path.exists():
            shutil.copy2(db_path, db_path.with_name(f"{db_path.name}.before-restore-{stamp}"))
        with tempfile.TemporaryDirectory() as tmp:
            z.extractall(tmp)
            shutil.copy2(Path(tmp) / "towerai.db", db_path)
            for name, key in DATA_DIRS.items():
                src, dst = Path(tmp) / name, Path(getattr(config, key))
                if src.is_dir():
                    if dst.is_dir():
                        dst.rename(dst.with_name(f"{dst.name}.before-restore-{stamp}"))
                    shutil.copytree(src, dst)
    return db_path


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv[:1] == ["create"]:
        print(f"Backup written: {create(argv[1] if len(argv) > 1 else None)}")
    elif argv[:1] == ["restore"] and len(argv) == 2:
        print(f"Restored into {restore(argv[1])}. Start the server again.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
