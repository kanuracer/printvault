"""Temporary health endpoint until the FastAPI application exists."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class PlaceholderHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"starting"}\n')
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *_args: object) -> None:
        del format
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8000), PlaceholderHandler).serve_forever()
