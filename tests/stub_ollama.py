"""Stands in for `ollama serve` in the tests: answers on OLLAMA_HOST and "pulls" into OLLAMA_MODELS."""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, body):
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(b'{"version": "stub"}')

    def do_POST(self):
        model = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["model"]
        if model == "missing:1":
            return self._send(b'{"error": "pull model manifest: file does not exist"}\n')
        with open(os.path.join(os.environ["OLLAMA_MODELS"], model.replace(":", "_")), "w") as f:
            f.write("weights")
        return self._send(b'{"status": "pulling manifest"}\n{"status": "success"}\n')


host, port = os.environ["OLLAMA_HOST"].split(":")
HTTPServer((host, int(port)), Handler).serve_forever()
