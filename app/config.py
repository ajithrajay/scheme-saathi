import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("SCHEMESAATHI_DATA", str(ROOT / "runtime"))))
    token: str = field(default_factory=lambda: os.getenv("SCHEMESAATHI_OWNER_KEY") or secrets.token_urlsafe(32))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", os.getenv("SCHEMESAATHI_PORT", "8765"))))
    headless: bool = field(default_factory=lambda: os.getenv("SCHEMESAATHI_HEADLESS") == "1" or os.getenv("SCHEMESAATHI_MODE") == "cloud")
    mode: str = field(default_factory=lambda: os.getenv("SCHEMESAATHI_MODE", "local"))
    public_url: str = field(default_factory=lambda: (os.getenv("SCHEMESAATHI_PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/"))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    encryption_key: str = field(default_factory=lambda: os.getenv("SCHEMESAATHI_ENCRYPTION_KEY", ""))
    demo_default: bool = field(default_factory=lambda: os.getenv("SCHEMESAATHI_DEMO_DEFAULT", "1") == "1")
    max_browsers: int = field(default_factory=lambda: int(os.getenv("SCHEMESAATHI_MAX_BROWSERS", "1")))

    def validate(self):
        if self.mode not in {"local", "cloud"}:
            raise ValueError("SCHEMESAATHI_MODE must be local or cloud")
        if not 1 <= self.max_browsers <= 3:
            raise ValueError("SCHEMESAATHI_MAX_BROWSERS must be 1 to 3")
        if self.mode == "cloud":
            url = urlparse(self.public_url)
            if url.scheme != "https" or not url.hostname or url.path or url.query or url.fragment or url.username:
                raise ValueError("Set SCHEMESAATHI_PUBLIC_URL to the HTTPS origin, without a path")
            if not os.getenv("SCHEMESAATHI_OWNER_KEY") or len(self.token) < 32:
                raise ValueError("Set SCHEMESAATHI_OWNER_KEY to a random secret of at least 32 characters")
            if not self.database_url or not self.encryption_key:
                raise ValueError("Cloud mode requires DATABASE_URL and SCHEMESAATHI_ENCRYPTION_KEY")
            if "sslmode=require" not in self.database_url and "sslmode=verify-full" not in self.database_url:
                raise ValueError("Cloud DATABASE_URL must require TLS (sslmode=require or verify-full)")
        if self.database_url and "-pooler." in (urlparse(self.database_url).hostname or ""):
            raise ValueError("Use the direct PostgreSQL URL, with Neon connection pooling disabled")
        if self.database_url and self.max_browsers != 1:
            raise ValueError("PostgreSQL mode supports one application browser at a time")
        if self.database_url and not self.encryption_key:
            raise ValueError("PostgreSQL storage requires a stable SCHEMESAATHI_ENCRYPTION_KEY")

    @property
    def base_url(self):
        # Practice browser requests stay inside the process's local network.
        return f"http://127.0.0.1:{self.port}"

    @property
    def allowed_hosts(self):
        return ["localhost", "127.0.0.1", "testserver"] + ([urlparse(self.public_url).hostname] if self.public_url else [])
