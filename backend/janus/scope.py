"""Scope (ARCHITECTURE.md §6).

Sintaxis de una regla (todo opcional salvo el host):

    [esquema://]host[:puerto][/path]

  - host admite comodines: ``*`` (cualquier cosa) y ``?`` (un carácter).
    ``*.target.com`` matchea ``target.com`` y cualquier subdominio.
  - path es un glob sobre el path sin query: ``/api/*``.
  - sin esquema/puerto/path = cualquiera.

Semántica:
  - Sin reglas include habilitadas, todo está en scope (salvo exclusiones).
  - Con reglas include, en scope = matchea alguna include y ninguna exclude.
  - Las exclusiones de host puro (sin path) además alimentan ``ignore_hosts``
    de mitmproxy: ese tráfico ni se descifra (pasa por TLS sin tocar), lo que
    evita romper apps con certificate pinning.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import asdict, dataclass

_RULE_RE = re.compile(
    r"^(?:(?P<scheme>[a-z][a-z0-9+.-]*)://)?"
    r"(?P<host>\[[^\]]+\]|[^/:]+)"
    r"(?::(?P<port>\d+|\*))?"
    r"(?P<path>/.*)?$",
    re.I,
)


class PatternError(ValueError):
    pass


@dataclass(frozen=True)
class Pattern:
    scheme: str | None
    host: str
    port: int | None
    path: str | None

    @classmethod
    def parse(cls, text: str) -> Pattern:
        text = text.strip()
        m = _RULE_RE.match(text)
        if not text or not m:
            raise PatternError(f"patrón inválido: {text!r}")
        host = m.group("host").strip("[]").lower()
        if not host:
            raise PatternError(f"falta el host en {text!r}")
        port = m.group("port")
        path = m.group("path")
        return cls(
            scheme=(m.group("scheme") or "").lower() or None,
            host=host,
            port=int(port) if port and port != "*" else None,
            path=path if path and path not in ("/", "/*") else None,
        )

    def host_matches(self, host: str) -> bool:
        host = host.lower()
        if fnmatch.fnmatchcase(host, self.host):
            return True
        # "*.target.com" también cubre el dominio pelado
        return self.host.startswith("*.") and host == self.host[2:]

    def matches(self, scheme: str, host: str, port: int, path: str) -> bool:
        if self.scheme and self.scheme != scheme.lower():
            return False
        if self.port is not None and self.port != port:
            return False
        if not self.host_matches(host):
            return False
        if self.path:
            bare = path.split("?", 1)[0] or "/"
            return fnmatch.fnmatchcase(bare, self.path)
        return True

    def ignore_regex(self) -> str | None:
        """Regex para ``ignore_hosts`` (matchea contra "host:puerto")."""
        if self.path or self.scheme == "http":
            return None
        host = self.host
        if host.startswith("*."):
            core = _glob_to_regex(host[2:])
            host_rx = rf"(?:[^:]+\.)?{core}"
        else:
            host_rx = _glob_to_regex(host)
        port_rx = str(self.port) if self.port is not None else r"\d+"
        return rf"^{host_rx}:{port_rx}$"


def _glob_to_regex(glob: str) -> str:
    out = []
    for ch in glob:
        if ch == "*":
            out.append(r"[^:]*")
        elif ch == "?":
            out.append(r"[^:]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


@dataclass
class Rule:
    id: int
    kind: str  # include | exclude
    matcher: str
    enabled: bool = True

    def to_json(self) -> dict:
        return asdict(self)


class Scope:
    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._rules: list[Rule] = []
        self._compiled: list[tuple[Rule, Pattern]] = []
        self.set_rules(rules or [])

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)

    def set_rules(self, rules: list[Rule]) -> None:
        compiled = []
        for rule in rules:
            if rule.kind not in ("include", "exclude"):
                raise PatternError(f"tipo de regla inválido: {rule.kind!r}")
            compiled.append((rule, Pattern.parse(rule.matcher)))
        self._rules = list(rules)
        self._compiled = compiled

    def evaluate(self, scheme: str, host: str, port: int, path: str) -> tuple[bool, Rule | None]:
        """(en_scope, regla_decisiva)."""
        active = [(r, p) for r, p in self._compiled if r.enabled]
        for rule, pat in active:
            if rule.kind == "exclude" and pat.matches(scheme, host, port, path):
                return False, rule
        includes = [(r, p) for r, p in active if r.kind == "include"]
        if not includes:
            return True, None
        for rule, pat in includes:
            if pat.matches(scheme, host, port, path):
                return True, rule
        return False, None

    def in_scope(self, scheme: str, host: str, port: int, path: str) -> bool:
        return self.evaluate(scheme, host, port, path)[0]

    @property
    def has_includes(self) -> bool:
        return any(r.enabled and r.kind == "include" for r in self._rules)

    def passthrough(self, host: str, port: int) -> bool:
        """¿mitmproxy dejaría pasar este host sin descifrar? (misma lógica que ignore_hosts)."""
        addr = f"{host}:{port}"
        return any(re.search(rx, addr, re.IGNORECASE) for rx in self.ignore_hosts())

    def ignore_hosts(self) -> list[str]:
        out = []
        for rule, pat in self._compiled:
            if rule.enabled and rule.kind == "exclude":
                rx = pat.ignore_regex()
                if rx:
                    out.append(rx)
        return out


# --- persistencia -----------------------------------------------------------

def load_rules(conn) -> list[Rule]:
    rows = conn.execute(
        "SELECT id, kind, matcher, enabled FROM scope_rules WHERE project_id = 1 ORDER BY position, id"
    ).fetchall()
    return [Rule(r["id"], r["kind"], r["matcher"], bool(r["enabled"])) for r in rows]


def save_rules(conn, rules: list[dict]) -> list[Rule]:
    conn.execute("DELETE FROM scope_rules WHERE project_id = 1")
    for pos, r in enumerate(rules):
        conn.execute(
            "INSERT INTO scope_rules (project_id, kind, matcher, enabled, position) VALUES (1, ?, ?, ?, ?)",
            (r["kind"], r["matcher"].strip(), 1 if r.get("enabled", True) else 0, pos),
        )
    return load_rules(conn)
