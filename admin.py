"""Admin and scheduling pages.

/admin/tools   (admins) — who may use each agent tool: everyone / admins / off,
               and who is an admin.
/schedules     (members) — recurring assistant tasks: create, run now, delete.
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import agent
import db
import scheduler
from auth import admin_required, current_user, login_required

bp = Blueprint("admin", __name__)


@bp.route("/admin/tools", methods=["GET", "POST"])
@admin_required
def tools():
    if request.method == "POST":
        perms = {n: request.form.get(f"perm_{n}", "everyone") for n in agent.TOOLS}
        db.set_setting("tool_permissions",
                       {n: p for n, p in perms.items() if p in agent.PERMISSIONS and p != "everyone"})
        admins = {int(x) for x in request.form.getlist("admin")}
        admins.add(current_user()["id"])  # never remove yourself
        with db.get_db() as c:
            c.execute("UPDATE users SET is_admin = 0")
            c.executemany("UPDATE users SET is_admin = 1 WHERE id = ?", [(i,) for i in admins])
        flash("Saved.", "ok")
        return redirect(url_for("admin.tools"))
    return render_template("admin_tools.html", tools=agent.TOOLS, perms=agent.tool_permissions(),
                           choices=agent.PERMISSIONS, users=db.list_users())


@bp.route("/schedules", methods=["GET", "POST"])
@login_required
def schedules():
    if request.method == "POST":
        f = request.form
        try:
            scheduler.create(f.get("room", "general").strip(), f.get("prompt"), f.get("kind"),
                             f.get("at_time"), f.get("weekday"), f.get("every_min"),
                             current_user()["id"])
            flash("Scheduled.", "ok")
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("admin.schedules"))
    return render_template("schedules.html", items=scheduler.list_all(), describe=scheduler.describe,
                           weekdays=scheduler.WEEKDAYS, rooms=db.list_rooms())


@bp.route("/admin/training")
@admin_required
def training():
    import learning
    return render_template("training.html", stats=learning.stats(), examples=learning.examples()[:30])


@bp.route("/admin/training/export.jsonl")
@admin_required
def training_export():
    import learning
    from flask import Response
    return Response(learning.export_jsonl(), mimetype="application/jsonl",
                    headers={"Content-Disposition": 'attachment; filename="towerai-finetune.jsonl"'})


@bp.route("/admin/backup")
@admin_required
def backup_page():
    import backup
    import config
    return render_template("backup.html", backups=backup.list_backups(), backup_dir=config.BACKUP_DIR,
                           every=config.BACKUP_EVERY_HOURS, keep=config.BACKUP_KEEP)


@bp.route("/admin/backup/download", methods=["POST"])
@admin_required
def backup_download():
    import tempfile
    import backup
    from flask import send_file
    path = backup.create(tempfile.mkdtemp(prefix="towerai-dl-"))
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="application/zip")


def _own_or_admin(sid):
    s = scheduler.get(sid)
    if not s:
        abort(404)
    user = current_user()
    if not (user["is_admin"] or s["created_by"] == user["id"]):
        abort(403)
    return s


@bp.route("/schedules/<int:sid>/delete", methods=["POST"])
@login_required
def delete_schedule(sid):
    _own_or_admin(sid)
    scheduler.delete(sid)
    return redirect(url_for("admin.schedules"))


@bp.route("/schedules/<int:sid>/run", methods=["POST"])
@login_required
def run_schedule(sid):
    import threading
    s = _own_or_admin(sid)
    threading.Thread(target=scheduler.run_task, args=(s,), daemon=True).start()
    flash(f"Running now — the answer will appear in #{s['room']}.", "ok")
    return redirect(url_for("admin.schedules"))


def register(app):
    app.register_blueprint(bp)
