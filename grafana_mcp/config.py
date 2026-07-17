import os
from dataclasses import dataclass, field


@dataclass
class GrafanaConfig:
    base_url: str = field(default_factory=lambda: os.environ.get("GRAFANA_URL", "https://grafana.cloudtrust.rocks").rstrip("/"))
    org_id: int = 1
    tls_verify: bool = field(default_factory=lambda: os.environ.get("GRAFANA_TLS_VERIFY", "false").lower() not in ("false", "0", "no"))


@dataclass
class OktaConfig:
    org: str = field(default_factory=lambda: os.environ.get("OKTA_ORG", "https://informatica.okta.com"))
    client_id: str = field(default_factory=lambda: os.environ.get("OKTA_CLIENT_ID", "0oa1g5gbes9bwDlFL1d8"))
    redirect_uri: str = field(default_factory=lambda: os.environ.get("GRAFANA_URL", "https://grafana.cloudtrust.rocks").rstrip("/") + "/login/generic_oauth")


@dataclass
class AuthConfig:
    session_file: str = field(default_factory=lambda: os.environ.get("SESSION_FILE", ".grafana-session.json"))
    refresh_before_expiry_ms: int = 3 * 60 * 1000  # 3 minutes


@dataclass
class ThresholdConfig:
    cpu_percent: float = field(default_factory=lambda: float(os.environ.get("THRESHOLD_CPU", 85)))
    memory_percent: float = field(default_factory=lambda: float(os.environ.get("THRESHOLD_MEMORY", 90)))
    thread_count: float = field(default_factory=lambda: float(os.environ.get("THRESHOLD_THREADS", 100)))
    error_rate: float = field(default_factory=lambda: float(os.environ.get("THRESHOLD_ERROR_RATE", 5)))
    response_time_ms: float = field(default_factory=lambda: float(os.environ.get("THRESHOLD_RESPONSE_MS", 3000)))


@dataclass
class Config:
    grafana: GrafanaConfig = field(default_factory=GrafanaConfig)
    okta: OktaConfig = field(default_factory=OktaConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)


config = Config()
