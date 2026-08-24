"""Authentication: register, login, logout, and a login_required guard.

This is the layer the old app.py never had — every collaboration route sits
behind it. Passwords are hashed with werkzeug (ships with Flask).
"""
from functools import wraps

from flask import (
    Blueprint, render_template, request, redirect, url_for, session, flash, g
)
from werkzeug.security import generate_password_hash, check_password_hash

import config
import db

bp = Blueprint("auth", __name__)

_USERNAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def current_user():
    if "user" not in g:
        uid = session.get("user_id")
        g.user = db.get_user(uid) if uid else None
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def _valid_username(name: str) -> bool:
    return bool(name) and len(name) <= 32 and all(ch in _USERNAME_OK for ch in name)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not _valid_username(username):
            flash("Username: 1–32 chars, letters/numbers/_/- only.", "error")
        elif len(password) < config.MIN_PASSWORD_LEN:
            flash(f"Password must be at least {config.MIN_PASSWORD_LEN} characters.", "error")
        elif db.get_user_by_name(username):
            flash("That username is taken.", "error")
        else:
            uid = db.create_user(username, generate_password_hash(password))
            session.clear()
            session["user_id"] = uid
            session.permanent = True
            return redirect(url_for("chat.index"))
    return render_template("register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.get_user_by_name(username)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            nxt = request.args.get("next") or url_for("chat.index")
            return redirect(nxt)
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
