import httpx
import pytest

from fake_webshare import SALT, TOKEN, create_fake_app
from wsdarr.webshare import WebshareClient, WebshareError
from wsdarr.webshare.md5crypt import md5crypt, webshare_password_hash

# Vectors generated with the (now removed) stdlib ``crypt.crypt(pw, "$1$salt$")``.
VECTORS = [
    ("password", "AbCd1234", "$1$AbCd1234$cirw/L2mCGWC6TQoYr6RB1"),
    ("", "x", "$1$x$fwjfZtMwarkdetsjiQreU1"),
    ("a" * 40, "saltsalt", "$1$saltsalt$xbcEYb2v/vQerF.rDxN620"),
    ("heslo123ěšč", "Qw3rTy", "$1$Qw3rTy$qqzVL5mLLxbDQSQZzmk041"),
    ("x" * 17, "12345678", "$1$12345678$rhnGCK3NG5AO.7NWpAE/Y."),
]


@pytest.mark.parametrize(("password", "salt", "expected"), VECTORS)
def test_md5crypt_vectors(password, salt, expected):
    assert md5crypt(password, salt) == expected
    assert md5crypt(password, f"$1${salt}$") == expected


def test_password_hash_is_sha1_of_md5crypt():
    assert len(webshare_password_hash("pass", SALT)) == 40


def make_client(app=None, **kwargs) -> WebshareClient:
    app = app or create_fake_app()
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ws")
    return WebshareClient(
        kwargs.pop("username", "user"),
        kwargs.pop("password", "pass"),
        base_url="http://ws/api/",
        http=http,
        **kwargs,
    )


async def test_login_and_file_link():
    saved = {}
    client = make_client(save_token=lambda t: saved.update(token=t))
    link = await client.file_link("a1")
    assert link.startswith("http://ws/dl/a1/")
    assert client.token == TOKEN
    assert saved["token"] == TOKEN


async def test_bad_credentials():
    client = make_client(password="wrong")
    with pytest.raises(WebshareError) as exc:
        await client.login()
    assert exc.value.code == "LOGIN_FATAL_1"


async def test_search_parses_files_and_caches():
    app = create_fake_app()
    client = make_client(app)
    page = await client.search("breaking bad s01e02")
    names = {f.name for f in page.files}
    assert "Breaking.Bad.S01E02.720p.WEB-DL.CZ.titulky.mkv" in names
    assert page.total == len(page.files)
    f = next(f for f in page.files if f.ident == "a1")
    assert f.size == 3_000_000 and f.positive_votes == 1 and not f.password
    await client.search("breaking bad s01e02")
    assert sum(1 for c in app.state.calls if c[0] == "search") == 1


async def test_expired_token_relogin():
    app = create_fake_app()
    client = make_client(app, load_token=lambda: "stale-token")
    client._last_login = -1000  # pretend the last login is long ago
    link = await client.file_link("b1")
    assert "/dl/b1/" in link
    assert client.token == TOKEN


async def test_user_data():
    client = make_client()
    data = await client.user_data()
    assert data["vip_days"] == "42"
