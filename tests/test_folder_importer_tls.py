"""TLS verification is secure by default and can be disabled only for this importer."""

import importlib.util
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def importer(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts/import_recordings.py"
    spec = importlib.util.spec_from_file_location("daythread_importer_tls_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for key in tuple(os.environ):
        if key.startswith("DAYTHREAD_") or key == "SSL_CERT_FILE":
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    return module


def context_from_main(importer, tmp_path, monkeypatch, *, settings="", argv=()):
    (tmp_path / ".env").write_text(settings)
    clients = []
    base = importer.Uploader

    def capture(*args):
        client = base(*args)
        clients.append(client)
        return client

    monkeypatch.setattr(importer, "Uploader", capture)
    previous_umask = os.umask(0o077)
    try:
        assert importer.main([
            "--once", "--dry-run", "--folder", str(tmp_path / "empty"),
            "--settle-seconds", "0", *argv,
        ]) == 0
    finally:
        os.umask(previous_umask)
    handler = next(h for h in clients[0].opener.handlers
                   if isinstance(h, importer.urllib.request.HTTPSHandler))
    return handler._context or ssl.create_default_context()


@pytest.mark.parametrize("saved,argv,insecure", [
    ("", (), False),
    ("true", (), True),
    ("TRUE", (), True),
    ("false", (), False),
    ("invalid", (), False),
    ("false", ("--insecure",), True),
    ("true", ("--no-insecure",), False),
])
def test_tls_defaults_env_and_cli_precedence(
    importer, tmp_path, monkeypatch, caplog, saved, argv, insecure,
):
    caplog.set_level("WARNING", logger="daythread-import")
    context = context_from_main(importer, tmp_path, monkeypatch,
                                settings=f"DAYTHREAD_INSECURE={saved}\n", argv=argv)
    assert context.check_hostname is not insecure
    assert context.verify_mode == (ssl.CERT_NONE if insecure else ssl.CERT_REQUIRED)
    assert bool(caplog.records) is insecure
    assert ssl.create_default_context().verify_mode == ssl.CERT_REQUIRED


def test_exported_tls_option_overrides_env_file(importer, tmp_path, monkeypatch):
    monkeypatch.setenv("DAYTHREAD_INSECURE", "false")
    context = context_from_main(importer, tmp_path, monkeypatch,
                                settings="DAYTHREAD_INSECURE=true\n")
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


def test_insecure_does_not_require_a_configured_ca_file(importer, tmp_path, monkeypatch):
    context = context_from_main(importer, tmp_path, monkeypatch,
                                settings='SSL_CERT_FILE="/missing/company-ca.pem"\n',
                                argv=("--insecure",))
    assert not context.check_hostname and context.verify_mode == ssl.CERT_NONE


@pytest.fixture
def https_server(tmp_path):
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl is required to generate a temporary test certificate")
    certificate, key = tmp_path / "server.crt", tmp_path / "server.key"
    subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
                    "-keyout", str(key), "-out", str(certificate)],
                   capture_output=True, check=True)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = json.dumps({"connected": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}", certificate
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_real_https_rejects_untrusted_cert_unless_explicitly_allowed(
    importer, tmp_path, monkeypatch, https_server,
):
    url, certificate = https_server
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    client = importer.Uploader(url)
    for settings, argv, succeeds in [
        ("", (), False),
        ("", ("--insecure",), True),
        ("DAYTHREAD_INSECURE=true\n", ("--no-insecure",), False),
        (f'SSL_CERT_FILE="{certificate}"\n', (), True),
    ]:
        context = context_from_main(importer, tmp_path, monkeypatch, settings=settings, argv=argv)
        client.opener = importer.urllib.request.build_opener(
            importer.NoRedirect(), importer.urllib.request.HTTPSHandler(context=context))
        if succeeds:
            assert client.request("GET", "/v1/status") == {"connected": True}
        else:
            with pytest.raises(ValueError, match="Server unavailable"):
                client.request("GET", "/v1/status")


def test_insecure_also_skips_hostname_verification(
    importer, tmp_path, monkeypatch, https_server,
):
    url, certificate = https_server
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    client = importer.Uploader(url.replace("localhost", "127.0.0.1"))
    for argv, succeeds in [((), False), (("--insecure",), True)]:
        context = context_from_main(importer, tmp_path, monkeypatch,
                                    settings=f'SSL_CERT_FILE="{certificate}"\n', argv=argv)
        client.opener = importer.urllib.request.build_opener(
            importer.NoRedirect(), importer.urllib.request.HTTPSHandler(context=context))
        if succeeds:
            assert client.request("GET", "/v1/status") == {"connected": True}
        else:
            with pytest.raises(ValueError, match="IP address mismatch"):
                client.request("GET", "/v1/status")


def test_connection_failure_reports_reason_without_credentials(importer):
    class Offline:
        def open(self, *args, **kwargs):
            raise importer.urllib.error.URLError("connection refused")

    client = importer.Uploader("https://example.test", token="private-test-token")
    client.opener = Offline()
    with pytest.raises(ValueError) as caught:
        client.request("GET", "/v1/status")
    assert "connection refused" in str(caught.value)
    assert "private-test-token" not in str(caught.value)
