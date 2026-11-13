import zipfile

import backup
import config
import db


def test_backup_and_restore_round_trip(tmp_path, monkeypatch, client, room):
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    (files_dir / "a.txt").write_text("hello")
    monkeypatch.setattr(config, "FILES_DIR", str(files_dir))
    db.add_message(room["id"], client.user_id, "user", client.username, "before the backup")

    path = backup.create(tmp_path / "bk")
    with zipfile.ZipFile(path) as z:
        assert {"towerai.db", "files/a.txt"} <= set(z.namelist())

    # Restore into a separate database file so the shared test DB isn't touched.
    target = tmp_path / "restored.db"
    monkeypatch.setattr(config, "DB_PATH", str(target))
    (files_dir / "a.txt").write_text("changed")
    backup.restore(path)
    assert (files_dir / "a.txt").read_text() == "hello"
    assert any(p.name.startswith("files.before-restore") for p in tmp_path.iterdir())
    assert "before the backup" in [m["content"] for m in db.recent_messages(room["id"], limit=200)]


def test_automatic_backups_and_pruning(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "BACKUP_KEEP", 2)
    assert backup.maybe_auto() is not None
    assert backup.maybe_auto() is None  # not due yet
    for i in range(3):
        (tmp_path / f"towerai-backup-2020010{i}-000000.zip").write_bytes(b"x")
    backup.prune()
    assert len(backup.list_backups()) == 2


def test_backup_pages_are_admin_only(app):
    from test_harness import make_client
    admin, member = make_client(app, admin=True), make_client(app)
    assert member.get("/admin/backup").status_code == 403
    assert admin.get("/admin/backup").status_code == 200
    r = admin.post("/admin/backup/download")
    assert r.status_code == 200 and r.mimetype == "application/zip"
