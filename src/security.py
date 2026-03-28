"""Security middleware — IP/CIDR whitelist + Bearer token + rate limit + body size."""

import time
from ipaddress import ip_address, ip_network

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

NO_AUTH_PATHS = {"/health", "/ui"}
MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, allowed_ips: list[str], auth_token: str = "",
                 rate_limit: int = 60, rate_window: int = 60,
                 max_body: int = MAX_BODY_BYTES):
        super().__init__(app)
        # Separate plain IPs and CIDR networks
        self._plain_ips: set[str] = set()
        self._networks: list = []
        for entry in allowed_ips:
            if "/" in entry:
                self._networks.append(ip_network(entry, strict=False))
            else:
                self._plain_ips.add(entry)
        self.auth_token = auth_token
        self.rate_limit = rate_limit
        self.rate_window = rate_window
        self.max_body = max_body
        self._hits: dict[str, list[float]] = {}  # ip -> [timestamps]

    def _ip_allowed(self, ip: str) -> bool:
        if not self._plain_ips and not self._networks:
            return True
        if ip in self._plain_ips:
            return True
        try:
            addr = ip_address(ip)
            return any(addr in net for net in self._networks)
        except ValueError:
            return False

    def _rate_ok(self, ip: str) -> bool:
        if self.rate_limit <= 0:
            return True
        now = time.monotonic()
        cutoff = now - self.rate_window
        hits = self._hits.get(ip, [])
        hits = [t for t in hits if t > cutoff]
        if len(hits) >= self.rate_limit:
            self._hits[ip] = hits
            return False
        hits.append(now)
        self._hits[ip] = hits
        return True

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else ""

        if not self._ip_allowed(client_ip):
            return JSONResponse({"error": "forbidden"}, status_code=403)

        if self.auth_token and request.url.path not in NO_AUTH_PATHS:
            if not request.url.path.startswith("/static"):
                auth = request.headers.get("authorization", "")
                if auth != f"Bearer {self.auth_token}":
                    return JSONResponse({"error": "unauthorized"}, status_code=401)

        if not self._rate_ok(client_ip):
            return JSONResponse({"error": "rate_limit_exceeded"}, status_code=429)

        # Body size check (Content-Length header, fast path)
        cl = request.headers.get("content-length")
        if cl and int(cl) > self.max_body:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)

        return await call_next(request)
