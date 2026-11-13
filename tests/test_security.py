import db
from werkzeug.security import generate_password_hash


def test_post_without_csrf_token_is_rejected(app, client):
    del client.environ_base["HTTP_X_CSRF_TOKEN"]
    r = client.post("/api/rooms/general/messages", json={"content": "hi"})
    assert r.status_code == 400
    r = client.post("/meetings/start", data={"room": "general"})
    assert r.status_code == 400


def test_csrf_token_as_header_or_form_field(client):
    assert client.post("/api/rooms/general/messages", json={"content": "hi"}).status_code == 200
    del client.environ_base["HTTP_X_CSRF_TOKEN"]
    r = client.post("/meetings/start", data={"room": "general", "csrf_token": "tok"})
    assert r.status_code == 302


def test_pages_embed_the_token(client):
    page = client.get("/rooms/general").get_data(as_text=True)
    assert 'name="csrf-token" content="tok"' in page and "X-CSRF-Token" in page


def test_device_api_is_exempt(client):
    del client.environ_base["HTTP_X_CSRF_TOKEN"]
    r = client.post("/api/stt/transcribe", data=b"x")
    assert r.status_code == 401  # API-key auth failure, not a CSRF 400


def test_websocket_from_another_site_is_refused(client):
    r = client.get("/ws/rooms/general", headers={"Upgrade": "websocket", "Origin": "http://evil.example"})
    assert r.status_code == 403


def test_login_throttling(app):
    db.create_user("throttled", generate_password_hash("right-pass"))
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf_token"] = "t"
    form = {"username": "throttled", "password": "wrong", "csrf_token": "t"}
    for _ in range(5):
        assert c.post("/login", data=form).status_code == 200
    form["password"] = "right-pass"
    assert c.post("/login", data=form).status_code == 429


def test_login_next_cannot_leave_the_site(app):
    db.create_user("redir", generate_password_hash("pw12"))
    c = app.test_client()
    with c.session_transaction() as s:
        s["csrf_token"] = "t"
    for nxt in ("//evil.example", "https://evil.example/x", "/\\evil.example"):
        r = c.post(f"/login?next={nxt}", data={"username": "redir", "password": "pw12", "csrf_token": "t"})
        assert r.status_code == 302 and "evil" not in r.headers["Location"]
        with c.session_transaction() as s:
            s["csrf_token"] = "t"
    r = c.post("/login?next=/meetings", data={"username": "redir", "password": "pw12", "csrf_token": "t"})
    assert r.headers["Location"].endswith("/meetings")


def test_logout_needs_post(client):
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 302


def test_health_hides_details_from_anonymous_callers(app, client):
    anon = app.test_client().get("/health").json
    assert set(anon) == {"ok", "llm_ok"}
    assert "llm_endpoint" in client.get("/health").json
