import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from leadfinder.net import HttpClient

PAGES = {
    "/robots.txt": (200, "User-agent: *\nDisallow: /privado\n"),
    "/": (200, "<html><title>Oi</title><body>Tel (61) 3333-4444</body></html>"),
    "/privado/x": (200, "segredo"),
    "/velho": (301, "/"),
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        status, body = PAGES.get(self.path, (404, "nada"))
        self.send_response(status)
        if status == 301:
            self.send_header("Location", body)
            body = ""
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())


@pytest.fixture()
def base(monkeypatch):
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_pagina_robots_e_redirecionamento(base):
    c = HttpClient(timeout=5, min_interval=0)
    r = c.get_page(base + "/")
    assert r.ok and "3333-4444" in r.text
    blocked = c.get_page(base + "/privado/x")
    assert blocked.error == "robots" and blocked.blocked and blocked.text is None
    moved = c.get_page(base + "/velho")
    assert moved.ok and moved.final_url.rstrip("/") == base


def test_servidor_inexistente_e_inacessivel():
    c = HttpClient(timeout=3, min_interval=0)
    r = c.get_page("http://127.0.0.1:9/")  # porta fechada
    assert not r.ok and r.unreachable
