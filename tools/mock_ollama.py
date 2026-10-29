#!/usr/bin/env python3
"""
tools/mock_ollama.py  -  a tiny FAKE Ollama server for development and CI.

It answers the three endpoints our client uses (/api/tags, /api/version, /api/chat) with
canned but plausible JSON, so teammates without a GPU (or the CI) can test the whole
pipeline - including the LLM code paths - without downloading a model.

    python tools/mock_ollama.py            # listens on http://localhost:11434
    python tools/mock_ollama.py 11500      # custom port (then set "host" in the rules llm block)

NEVER use it for real grading: the answers are fixed, not produced by a model.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/tags"):
            self._send({"models": [{"name": "llama3.1:8b"}]})
        else:
            self._send({"version": "mock-0.0"})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        system = req["messages"][0]["content"]
        if "READABILITY" in system:
            out = {"naming": 3, "comments": 2, "formatting": 5, "structure": 4, "score": 4,
                   "reasons": ["single-letter names", "no comments"], "suggestions": ["Rename a,b to lhs,rhs"]}
            text = json.dumps(out)
        elif "adversarial software tester" in system:
            text = json.dumps({"cases": [{"input": "-2147483648 -2147483648\n", "reason": "min int + min int overflow"},
                                         {"input": "5\n7\n", "reason": "numbers on separate lines"}]})
        elif "tutor" in system:
            text = "The sum a + b is computed in a 32-bit int, which overflows for large operands; use long long."
        elif "closing paragraph" in system:
            text = "Good core logic. Improve modularity and handle overflow with a wider integer type."
        else:
            text = json.dumps({"likely_ai": False, "confidence": 0.2, "signals": []})
        self._send({"message": {"role": "assistant", "content": text}})

    def log_message(self, *a):   # keep the console quiet
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11434
    print(f"mock Ollama on http://localhost:{port}  (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
