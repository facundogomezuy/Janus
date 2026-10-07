"""Tests unitarios: mensajes HTTP, scope y settings."""
from __future__ import annotations

import gzip

import pytest

from janus import httpmsg
from janus.scope import Pattern, PatternError, Rule, Scope
from janus.settings import Settings, SettingsError


# --- httpmsg ------------------------------------------------------------------

def test_split_and_parse_request_lf_and_crlf():
    for sep in (b"\n", b"\r\n"):
        data = sep.join([b"POST /login?x=1 HTTP/1.1", b"Host: t.com", b"X-A:  b ", b"", b'{"a":1}'])
        req = httpmsg.parse_request(data)
        assert (req.method, req.target, req.http_version) == ("POST", "/login?x=1", "HTTP/1.1")
        assert req.headers == [("Host", "t.com"), ("X-A", "b")]
        assert req.body == b'{"a":1}'


def test_parse_response():
    res = httpmsg.parse_response(b"HTTP/1.1 404 Not Found\r\nA: 1\r\n\r\nnope")
    assert (res.status_code, res.reason, res.body) == (404, "Not Found", b"nope")


def test_prepare_for_wire_normalizes_head_and_fixes_content_length():
    text = b"POST / HTTP/1.1\nHost: t.com\nContent-Length: 999\n\nhola"
    wire = httpmsg.prepare_request_for_wire(text)
    assert wire == b"POST / HTTP/1.1\r\nHost: t.com\r\nContent-Length: 4\r\n\r\nhola"


def test_prepare_for_wire_adds_content_length_and_keeps_header_formatting():
    text = b"PUT /x HTTP/1.1\nhost:t.com\nX-Weird :  spaced\n\nabc"
    wire = httpmsg.prepare_request_for_wire(text)
    assert wire.startswith(b"PUT /x HTTP/1.1\r\nhost:t.com\r\nX-Weird :  spaced\r\n")
    assert b"Content-Length: 3\r\n\r\nabc" in wire


def test_prepare_for_wire_respects_flag_and_transfer_encoding():
    text = b"POST / HTTP/1.1\nHost: t\nContent-Length: 1\n\nabcdef"
    assert b"Content-Length: 1\r\n" in httpmsg.prepare_request_for_wire(text, fix_content_length=False)
    chunked = b"POST / HTTP/1.1\nHost: t\nTransfer-Encoding: chunked\n\n3\r\nabc\r\n0\r\n\r\n"
    assert b"Content-Length" not in httpmsg.prepare_request_for_wire(chunked)


def test_prepare_for_wire_without_blank_line():
    assert httpmsg.prepare_request_for_wire(b"GET / HTTP/1.1\nHost: t") == b"GET / HTTP/1.1\r\nHost: t\r\n\r\n"


def test_restore_body_reuses_original_crlf_bytes():
    original = b"line1\r\nline2\r\n"
    edited = b"line1\nline2\n"  # lo que devuelve un textarea
    assert httpmsg.restore_body(edited, original, []) == original
    assert httpmsg.restore_body(b"otra cosa", original, []) == b"otra cosa"


def test_restore_body_multipart_gets_crlf():
    headers = [("Content-Type", "multipart/form-data; boundary=x")]
    edited = b"--x\nContent-Disposition: form-data; name=a\n\n1\n--x--\n"
    out = httpmsg.restore_body(edited, None, headers)
    assert b"\n" not in out.replace(b"\r\n", b"")


def test_decode_body_gzip_and_view():
    raw = gzip.compress(b'{"ok": true}')
    headers = [("Content-Encoding", "gzip"), ("Content-Type", "application/json")]
    decoded, err = httpmsg.decode_body(raw, headers)
    assert decoded == b'{"ok": true}' and err is None
    view = httpmsg.body_view(raw, headers)
    assert view["text"] == '{"ok": true}' and view["size"] == len(raw)


def test_decode_body_bad_encoding_is_reported():
    decoded, err = httpmsg.decode_body(b"not gzip", [("Content-Encoding", "gzip")])
    assert decoded is None and "gzip" in err


def test_binary_body_has_no_text():
    view = httpmsg.body_view(b"\x89PNG\r\n\x1a\n\x00\x00", [("Content-Type", "image/png")])
    assert view["text"] is None


def test_text_roundtrip_latin1():
    data = bytes(range(256))
    payload = httpmsg.message_payload(data)
    assert payload["encoding"] == "latin-1"
    assert httpmsg.text_to_bytes(payload["text"], payload["encoding"]) == data


def test_editable_message_shows_decoded_body():
    raw = gzip.compress(b"hello")
    payload, decoded = httpmsg.editable_message("HTTP/1.1 200 OK", [("Content-Encoding", "gzip")], raw)
    assert decoded and payload["text"].endswith("\r\n\r\nhello")


# --- scope ----------------------------------------------------------------------

@pytest.mark.parametrize(("pattern", "url", "expected"), [
    ("*.target.com", ("https", "api.target.com", 443, "/"), True),
    ("*.target.com", ("https", "target.com", 443, "/"), True),
    ("*.target.com", ("https", "eviltarget.com", 443, "/"), False),
    ("target.com", ("http", "TARGET.com", 80, "/x"), True),
    ("https://target.com", ("http", "target.com", 80, "/"), False),
    ("target.com:8443", ("https", "target.com", 443, "/"), False),
    ("target.com:8443", ("https", "target.com", 8443, "/"), True),
    ("target.com/api/*", ("https", "target.com", 443, "/api/users?id=1"), True),
    ("target.com/api/*", ("https", "target.com", 443, "/static/app.js"), False),
    ("10.0.0.?", ("http", "10.0.0.7", 80, "/"), True),
])
def test_pattern_matches(pattern, url, expected):
    assert Pattern.parse(pattern).matches(*url) is expected


def test_pattern_invalid():
    with pytest.raises(PatternError):
        Pattern.parse("")
    with pytest.raises(PatternError):
        Pattern.parse("https://")


def test_scope_semantics():
    s = Scope()
    assert s.in_scope("https", "anything.com", 443, "/")  # sin reglas: todo
    s.set_rules([
        Rule(1, "include", "*.target.com"),
        Rule(2, "exclude", "cdn.target.com"),
        Rule(3, "include", "other.com", enabled=False),
    ])
    assert s.in_scope("https", "api.target.com", 443, "/")
    assert not s.in_scope("https", "cdn.target.com", 443, "/")
    assert not s.in_scope("https", "other.com", 443, "/")
    assert s.ignore_hosts() == [r"^cdn\.target\.com:\d+$"]
    assert s.passthrough("CDN.target.com", 443)
    assert not s.passthrough("api.target.com", 443)


def test_ignore_regex_wildcard_and_path_rules():
    assert Pattern.parse("*.ads.com").ignore_regex() == r"^(?:[^:]+\.)?ads\.com:\d+$"
    assert Pattern.parse("ads.com/track/*").ignore_regex() is None  # path: solo filtra la vista


# --- settings -----------------------------------------------------------------------

def test_settings_validation():
    s = Settings({"proxy.listen_port": 9090, "unknown": 1, "proxy.http2": "garbage"})
    assert s["proxy.listen_port"] == 9090
    assert s["proxy.http2"] is True  # valor inválido -> default
    with pytest.raises(SettingsError):
        s.validate_update({"proxy.listen_port": 70000})
    with pytest.raises(SettingsError):
        s.validate_update({"proxy.upstream": "ftp://x"})
    assert s.validate_update({"proxy.upstream": "http://10.0.0.1:3128"})["proxy.upstream"] == "http://10.0.0.1:3128"


# --- repeater -----------------------------------------------------------------------

def test_repeater_tab_names_do_not_repeat(tmp_path):
    from janus import repeater
    from janus.db import Database

    db = Database(tmp_path / "t.sqlite3")
    try:
        def make(conn):
            return repeater.create_tab(conn, name=None, host="t", port=443, tls=True,
                                       raw_request=b"GET / HTTP/1.1\r\n\r\n", encoding="utf-8", orig_body=None)
        a, b, c = (db.write_sync(make) for _ in range(3))
        assert [a["name"], b["name"], c["name"]] == ["1", "2", "3"]
        db.write_sync(repeater.delete_tab, b["id"])
        assert db.write_sync(make)["name"] == "4"
        # nombres que isdigit() acepta pero int() no: no deben romper la numeración
        for odd in ("²", "①", "1" * 50):
            db.write_sync(repeater.update_tab, a["id"], {"name": odd})
            assert db.write_sync(make)["name"].isdecimal()
    finally:
        db.close()
