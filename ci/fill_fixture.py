"""Controlled HTTPS form for the local credential-fill check."""

import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>LeashByte fill fixture</title></head>
<body><h1>Controlled login form</h1><form>
<label for="username">User name</label><input id="username" name="username" autocomplete="username">
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password">
<label for="choice">Choice</label><select id="choice" name="choice"><option value="one">One</option><option value="two">Two</option></select>
<button type="button" style="position:fixed;top:0;right:0" onclick="document.body.dataset.clicked='yes';this.textContent=(document.querySelector('#password').value.length?'Password filled':'Password empty')+', '+document.querySelector('#choice').value+', '+(window.scrollY>0?'scrolled':'at top')">Check state</button>
</form><div style="height:2000px">Controlled scroll area</div></body></html>"""

NEXT_PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Controlled navigation result</title></head>
<body><h1>Navigation succeeded</h1></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /\n"
            content_type = "text/plain"
        elif self.path == "/login":
            body = PAGE
            content_type = "text/html; charset=utf-8"
        elif self.path == "/next":
            body = NEXT_PAGE
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


server = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/run/fixture/cert.pem", "/run/fixture/key.pem")
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
