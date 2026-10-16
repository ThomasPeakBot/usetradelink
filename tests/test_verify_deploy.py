import functools
import http.server
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import verify_deploy  # noqa: E402
from build_site import build  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class Handler(http.server.SimpleHTTPRequestHandler):
    """Static server that behaves like Cloudflare Pages: dir/ -> index.html, 404.html for misses, _headers."""
    def log_message(self, *a):
        pass

    def end_headers(self):
        for h in ("X-Content-Type-Options: nosniff", "Referrer-Policy: x", "X-Frame-Options: SAMEORIGIN",
                  "Strict-Transport-Security: max-age=1"):
            k, v = h.split(": ")
            self.send_header(k, v)
        super().end_headers()

    def send_error(self, code, message=None, explain=None):
        if code == 404:
            body = open(os.path.join(self.directory, "404.html"), "rb").read()
            self.send_response(404)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)
        else:
            super().send_error(code, message, explain)


def serve(directory):
    handler = functools.partial(Handler, directory=directory)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


class VerifyDeployTests(unittest.TestCase):
    def test_public_build_passes(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out)
        build(ROOT, out)
        httpd, url = serve(out)
        self.addCleanup(httpd.shutdown)
        failed = [r for r in verify_deploy.verify(url) if not r[0]]
        self.assertEqual(failed, [])

    def test_repository_root_publish_is_detected(self):
        # Serving the raw repository (Cloudflare's current setting) exposes private files.
        httpd, url = serve(ROOT)
        self.addCleanup(httpd.shutdown)
        failed = [r[1] for r in verify_deploy.verify(url) if not r[0]]
        self.assertTrue(any(f.startswith("private file not served: /scripts/") for f in failed), failed)


if __name__ == "__main__":
    unittest.main()
