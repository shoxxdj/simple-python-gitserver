#!/usr/bin/env python3
"""gitdrop: chaque sous-dossier de ./repos devient un dépôt git cloneable en HTTP.

Les modifications du dossier sont re-commitées automatiquement à chaque clone/fetch.
Dépendances : python3 + git (git-http-backend). Rien d'autre.
"""
import argparse, os, subprocess, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--src", default="repos")
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--bind", default="0.0.0.0")
ap.add_argument("--host", default="", help="base URL publique à afficher (ex: https://xxx.ngrok.app)")
args = ap.parse_args()

SRC = Path(args.src).resolve()
BARE = SRC.parent / ".gitdrop-bare"
SRC.mkdir(exist_ok=True)
BARE.mkdir(exist_ok=True)

ENV = {**os.environ, "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@x",
       "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": "x@x"}


def sync(name):
    """Commit le contenu du dossier dans un dépôt bare, si changé."""
    work, bare = SRC / name, BARE / f"{name}.git"
    if not work.is_dir():
        return False
    if not bare.exists():
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True, env=ENV)
    g = ["git", f"--git-dir={bare}", f"--work-tree={work}"]
    subprocess.run(g + ["add", "-A", "-f"], check=True, env=ENV)
    if subprocess.run(g + ["diff", "--cached", "--quiet"], env=ENV).returncode != 0 or \
       subprocess.run(g + ["rev-parse", "-q", "--verify", "HEAD"], env=ENV, capture_output=True).returncode != 0:
        subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "update"], check=True, env=ENV)
    subprocess.run(["git", f"--git-dir={bare}", "update-server-info"], env=ENV)
    return True


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def handle_any(self):
        path, _, query = self.path.partition("?")
        parts = path.strip("/").split("/", 1)
        if not parts[0].endswith(".git") or not sync(parts[0][:-4]):
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        if self.headers.get("Transfer-Encoding") == "chunked":  # git peut chunker les gros POST
            body = b""
            while True:
                size = int(self.rfile.readline().strip() or b"0", 16)
                if size == 0:
                    self.rfile.readline(); break
                body += self.rfile.read(size); self.rfile.readline()
        env = {**ENV, "GIT_PROJECT_ROOT": str(BARE), "GIT_HTTP_EXPORT_ALL": "1",
               "REQUEST_METHOD": self.command, "PATH_INFO": path, "QUERY_STRING": query,
               "CONTENT_TYPE": self.headers.get("Content-Type", ""), "CONTENT_LENGTH": str(len(body)),
               "REMOTE_ADDR": self.client_address[0]}
        if self.headers.get("Content-Encoding"):
            env["HTTP_CONTENT_ENCODING"] = self.headers["Content-Encoding"]
        out = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True).stdout
        head, _, data = out.partition(b"\r\n\r\n")
        status, headers = 200, []
        for line in head.decode().split("\r\n"):
            k, _, v = line.partition(": ")
            if k.lower() == "status":
                status = int(v.split()[0])
            elif k:
                headers.append((k, v))
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = handle_any

    def log_message(self, fmt, *a):
        print(f"[{self.client_address[0]}] {self.command} {self.path} -> {a[1] if len(a) > 1 else ''}")


base = args.host.rstrip("/") or f"http://<IP>:{args.port}"
print(f"Dossiers dans {SRC}/ -> URLs :")
for d in sorted(p.name for p in SRC.iterdir() if p.is_dir()):
    print(f"  cargo install --git {base}/{d}.git")
print(f"(un nouveau dossier est pris en compte à chaud : {base}/<dossier>.git)\n")
ThreadingHTTPServer((args.bind, args.port), H).serve_forever()