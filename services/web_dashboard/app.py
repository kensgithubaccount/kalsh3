"""Private server-rendered M1 control center (WSGI, no Kalshi mutation routes)."""

from __future__ import annotations

import html
import json
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from email import policy
from email.parser import BytesParser
from http import cookies
from typing import Any
from urllib.parse import parse_qs

from services.reporting_service.support_snapshot import (
    support_snapshot_json,
    support_snapshot_markdown,
)
from services.risk_engine.authorization import AuthorizationError, AuthorizationStore, SystemClock
from services.risk_engine.policy import RiskPolicy

from .product import SURFACES, GlobalProductState, ProductSurface, derive_global_state, dollars
from .security import SecretBox, SecurityError, consume_recovery_code, verify_password, verify_totp
from .setup import SetupError, SetupService
from .store import StateStore

StartResponse = Callable[[str, list[tuple[str, str]]], None]

SECURITY_HEADERS = [
    (
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'",
    ),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
]


def _recent_risk_codes(summary: dict[str, object]) -> tuple[str, ...]:
    rows = summary.get("recent_events")
    if not isinstance(rows, tuple):
        return ()
    return tuple(str(row[0]) for row in rows if isinstance(row, tuple) and row)


def _risk_kill_labels(summary: dict[str, object]) -> dict[str, str]:
    rows = summary.get("kill_states")
    if not isinstance(rows, tuple):
        return {}
    return {
        str(row[0]): f"{row[1]} · {row[2]}"
        for row in rows
        if isinstance(row, tuple) and len(row) >= 3
    }


def _layout(
    title: str,
    body: str,
    csrf: str = "",
    current_path: str = "/",
    global_state: GlobalProductState = GlobalProductState.LEARNING,
) -> bytes:
    def nav_link(surface: ProductSurface) -> str:
        active = current_path == surface.path or (
            surface.path != "/" and current_path.startswith(surface.path + "/")
        )
        current = ' aria-current="page"' if active else ""
        return f'<a href="{surface.path}"{current}>{html.escape(surface.label)}</a>'

    nav = "".join(nav_link(surface) for surface in SURFACES)
    state_class = global_state.lower().replace(" ", "-")
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><link rel=stylesheet href=/static/app.css></head><body><a class=skip-link href=#main-content>Skip to main content</a><header><a class=brand href=/ aria-label="Kalshi Control Center home">Kalshi Control Center</a><nav aria-label="Primary product navigation">{nav}</nav></header><div class="global-state {state_class}" role=status aria-label="Global system state"><span>System state</span><strong>{html.escape(global_state)}</strong><span>Production writes OFF</span><span>Research influence NONE</span></div><main id=main-content tabindex=-1>{body}</main><footer><strong>Safety boundary</strong><span>PRODUCTION WRITES: <strong>OFF</strong></span><span>RESEARCH INFLUENCE: NONE</span><span>Simulations are not orders</span></footer></body></html>""".encode()


class DashboardApp:
    def __init__(
        self, store: StateStore, box: SecretBox, setup: SetupService | None = None
    ) -> None:
        self.store = store
        self.box = box
        self.setup = setup
        self.risk_store = AuthorizationStore(store.path, SystemClock())

    def __call__(self, environ: dict[str, Any], start: StartResponse) -> Iterable[bytes]:
        path, method = str(environ.get("PATH_INFO", "/")), str(environ.get("REQUEST_METHOD", "GET"))
        if path == "/healthz":
            return self._respond(
                start,
                "200 OK",
                b'{"autonomy":"OFF","production_state":"DISARMED","production_write_credential":"NONE","status":"ok"}',
                "application/json",
            )
        if path == "/readyz":
            return self._respond(
                start,
                "503 Service Unavailable",
                b'{"ready":false,"reason":"LIVE_DEPENDENCIES_NOT_VERIFIED","production_state":"DISARMED"}',
                "application/json",
            )
        if path == "/static/app.css":
            return self._respond(start, "200 OK", CSS.encode(), "text/css")
        if path == "/login":
            return self._login(environ, start)
        if path == "/setup" and not self.store.configured():
            return self._setup(environ, start)
        token = self._cookie(environ, "session")
        csrf = self.store.session_csrf(token, int(time.time())) if token else None
        if not self.store.configured():
            body = "<section class=hero><p class=eyebrow>SETUP REQUIRED</p><h1>Real account not connected</h1><p>Use the one-time HTTPS setup workflow before account data can be read.</p></section>"
            return self._respond(start, "200 OK", _layout("Setup required", body))
        if csrf is None:
            return self._redirect(start, "/login")
        if path == "/logout" and method == "POST":
            form = self._form(environ)
            if form.get("csrf", [""])[0] != csrf:
                return self._respond(start, "403 Forbidden", b"CSRF rejected")
            self.store.delete_session(token)
            self.store.audit("logout", "owner")
            return self._redirect(start, "/login", expire=True)
        if path in {"/risk/halt", "/risk/reset"} and method == "POST":
            form = self._form(environ)
            if form.get("csrf", [""])[0] != csrf:
                return self._respond(start, "403 Forbidden", b"CSRF rejected")
            if not self._risk_reauthenticated(form):
                self.store.audit("risk_control_reauthentication_failed", "owner")
                return self._respond(start, "403 Forbidden", b"Strong reauthentication rejected")
            reason = form.get("reason", [""])[0].strip()
            confirmation = form.get("confirmation", [""])[0]
            try:
                if path == "/risk/halt" and confirmation == "HALT NEW RISK":
                    self.risk_store.activate_global_halt(
                        actor="OWNER", reason=reason, authenticated=True
                    )
                    self.store.audit("global_halt_activated", "owner", reason)
                elif path == "/risk/reset" and confirmation == "RESET GLOBAL HALT":
                    self.risk_store.reset_global_halt(
                        actor="OWNER", reason=reason, strong_reauthenticated=True
                    )
                    self.store.audit("global_halt_reset", "owner", reason)
                else:
                    raise AuthorizationError("explicit confirmation text does not match")
            except AuthorizationError:
                return self._respond(start, "400 Bad Request", b"Risk control request rejected")
            return self._redirect(start, "/risk")
        state = self.store.refresh_state()
        stale = self._stale(state.last_success)
        warning = (
            '<div class="warning">Account data is stale or unavailable. Do not treat it as current.</div>'
            if stale
            else ""
        )
        snapshot = state.snapshot or {}
        universe = self.store.universe_summary()
        realtime = self.store.realtime_summary()
        semantics = self.store.semantic_summary()
        external = self.store.external_intelligence_summary()
        risk_summary = self.risk_store.safety_summary()
        global_state = derive_global_state(
            account_status=state.status,
            stale=stale,
            unresolved_gaps=int(realtime["unresolved_gaps"]),
            compliance_hold=risk_summary["compliance_state"] != "CLEAR",
            globally_halted=bool(risk_summary["global_halt"]),
        )
        if path.startswith("/breaking/"):
            body = self._signal_detail(
                path.removeprefix("/breaking/"),
                self.store.breaking_signal(path.removeprefix("/breaking/")),
            )
        elif path == "/breaking":
            body = self._breaking_now(external, self.store.breaking_signals())
        elif path == "/sources":
            body = self._sources(self.store.external_sources())
        elif path.startswith("/markets/"):
            ticker = path.removeprefix("/markets/")
            spec = self.store.semantic_spec(ticker)
            body = self._market_detail(
                ticker,
                spec,
                self.store.market_evidence(ticker),
                self.store.market_forecasts(ticker),
            )
        elif path == "/markets":
            query = parse_qs(str(environ.get("QUERY_STRING", "")))
            filters = {key: values[0] for key, values in query.items() if values}
            body = self._markets(
                universe, self.store.universe_markets(filters), filters, realtime, semantics
            )
        elif path == "/portfolio":
            body = warning + self._portfolio(snapshot)
        elif path == "/orders":
            body = warning + self._orders_and_trades(snapshot)
        elif path == "/demo/setup":
            body = self._demo_setup(csrf)
        elif path == "/forecasting":
            body = self._forecasting(self.store.forecasting_summary())
        elif path == "/learning":
            body = self._learning(self.store.learning_summary())
        elif path == "/opportunities":
            body = self._opportunities(self.store.opportunity_summary())
        elif path == "/backtests":
            body = self._backtests(self.store.execution_research_summary())
        elif path == "/risk":
            body = warning + self._risk_and_safety(
                snapshot,
                global_state,
                risk_summary,
                self.store.risk_evaluations(),
                csrf,
            )
        elif path == "/canary":
            body = self._supervised_canary()
        elif path == "/autonomy":
            body = self._bounded_autonomy()
        elif path == "/advanced":
            body = self._advanced(
                self.store.forecasting_summary(),
                self.store.execution_research_summary(),
                self.store.learning_summary(),
            )
        elif path == "/system":
            body = warning + self._system(
                state,
                snapshot,
                universe,
                realtime,
                self.store.historical_replay_summary(),
                self.store.llm_evidence_summary(),
            )
        elif path == "/reports":
            body = (
                warning
                + "<section><p class=eyebrow>OPERATING &amp; GOVERNANCE REPORTS</p><h1>Reports</h1><div class=columns><article><h2>Daily operating brief</h2><p>Default schedule: 7:00 AM America/New_York. Account, exposure, reconciled activity, settlements, incidents, and owner issues.</p><span class=badge>NOT SCHEDULED</span></article><article><h2>Weekly learning report</h2><p>Default schedule: Monday. Forecast, source, model, execution-research, cost, and governance evidence.</p><span class=badge>NOT SCHEDULED</span></article><article><h2>Monthly governance report</h2><p>Policy, risk, source/model changes, costs, incidents, and human acceptance history.</p><span class=badge>NOT SCHEDULED</span></article></div><h2>Sanitized support exports</h2><p>Exports omit credentials and identifying transaction IDs.</p><div class=actions><a class=button href=/reports/support.md>Download Markdown</a><a class=button href=/reports/support.json>Download JSON</a></div></section>"
            )
        elif path in {"/reports/support.json", "/reports/support.md"}:
            content = (
                support_snapshot_json(
                    state,
                    {
                        "mode": "read_only",
                        "semantics": semantics,
                        "semantic_parser_versions": ["deterministic-v2"],
                        "risk": {
                            "policy_version": RiskPolicy().version,
                            "reserve_state": "NOT VERIFIED",
                            "active_capital_usage": "Unavailable",
                            "aggregate_risk": "Unavailable",
                            "loss_stop_state": "NOT VERIFIED",
                            "drawdown": "Unavailable",
                            "reconciliation_state": "NOT VERIFIED",
                            "kill_states": "NOT VERIFIED",
                            "compliance_state": risk_summary["compliance_state"],
                            "global_halt": risk_summary["global_halt"],
                            "recent_reason_codes": _recent_risk_codes(risk_summary),
                            "production_execution": {
                                "path": "IMPLEMENTED_OFFLINE_VERIFIED",
                                "credential_installed": False,
                                "signer_state": "DISARMED",
                                "real_money_order_executed": False,
                            },
                        },
                        "operations": {
                            "readiness": "NOT VERIFIED",
                            "backup": "NOT VERIFIED",
                            "restore_drill": "NOT VERIFIED",
                            "monthly_cost": "NOT VERIFIED",
                            "monthly_target_usd": "25.00",
                            "monthly_hard_cap_usd": "50.00",
                            "production_state": "DISARMED",
                            "autonomy": "OFF",
                        },
                    },
                )
                if path.endswith("json")
                else support_snapshot_markdown(state)
            )
            content_type = "application/json" if path.endswith("json") else "text/markdown"
            return self._respond(
                start, "200 OK", content.encode(), content_type, download=path.rsplit("/", 1)[-1]
            )
        elif path == "/":
            body = warning + self._overview(
                state.status,
                snapshot,
                universe,
                realtime,
                external,
                global_state,
                self.store.opportunity_summary(),
            )
        else:
            body = "<section class=hero><p class=eyebrow>NOT FOUND</p><h1>This page does not exist</h1><p>The requested dashboard surface is unavailable.</p><a class=button href=/>Return to overview</a></section>"
            body += f'<form method=post action=/logout><input type=hidden name=csrf value="{csrf}"><button>Log out</button></form>'
            return self._respond(
                start,
                "404 Not Found",
                _layout("Page not found", body, csrf, path, global_state),
            )
        body += f'<form method=post action=/logout><input type=hidden name=csrf value="{csrf}"><button>Log out</button></form>'
        return self._respond(
            start,
            "200 OK",
            _layout("Account control center", body, csrf, path, global_state),
        )

    def _setup(self, environ: dict[str, Any], start: StartResponse) -> Iterable[bytes]:
        if self.setup is None:
            return self._respond(start, "503 Service Unavailable", b"Setup service unavailable")
        if environ.get("REQUEST_METHOD") != "POST":
            body = """<section class=auth><p class=eyebrow>ONE-TIME SETUP</p><h1>Connect read-only account</h1><p>Upload the PEM directly over HTTPS. It is never sent to JavaScript or stored in localStorage.</p><form method=post enctype=multipart/form-data><label>Setup token<input type=password name=setup_token required></label><label>Owner username<input name=username required></label><label>Strong password<input type=password name=password required></label><label>TOTP secret from enrollment<input name=totp_secret required></label><label>Current six-digit code<input name=totp_code inputmode=numeric required></label><label>Read-only API key ID<input name=key_id required></label><label>PKCS#8 PEM file<input type=file name=pem accept=.pem required></label><button>Validate account 0 and finish</button></form></section>"""
            return self._respond(start, "200 OK", _layout("Secure setup", body))
        try:
            form = self._multipart(environ)
            secret = form["totp_secret"].decode()
            codes = self.setup.complete(
                setup_token=form["setup_token"].decode(),
                username=form["username"].decode(),
                password=form["password"].decode(),
                totp_secret=secret,
                totp_code_valid=verify_totp(secret, form["totp_code"].decode()),
                key_id=form["key_id"].decode(),
                private_key_pem=form["pem"],
            )
        except (KeyError, UnicodeDecodeError, SetupError, ValueError):
            return self._respond(
                start,
                "400 Bad Request",
                _layout(
                    "Setup failed",
                    "<h1>Setup failed safely</h1><p>No credential was stored. Verify the exact read scope, TOTP, account 0, and file.</p>",
                ),
            )
        body = (
            "<section><h1>Setup complete</h1><p>Save these one-use recovery codes now. They will not be shown again.</p><pre>"
            + html.escape("\n".join(codes))
            + "</pre><a class=button href=/login>Continue to login</a></section>"
        )
        return self._respond(start, "200 OK", _layout("Recovery codes", body))

    def _login(self, environ: dict[str, Any], start: StartResponse) -> Iterable[bytes]:
        if environ.get("REQUEST_METHOD") != "POST":
            return self._respond(
                start,
                "200 OK",
                _layout(
                    "Login",
                    "<section class=auth><h1>Owner login</h1><form method=post><label>Username<input name=username autocomplete=username required></label><label>Password<input type=password name=password autocomplete=current-password required></label><label>Authenticator or recovery code<input name=code inputmode=numeric required></label><button>Sign in</button></form></section>",
                ),
            )
        form, now = self._form(environ), int(time.time())
        username = form.get("username", [""])[0]
        identity = str(environ.get("REMOTE_ADDR", "unknown")) + ":" + username
        valid = self.store.login_allowed(identity, now)
        valid &= username == self.store.config("owner")
        valid &= verify_password(
            form.get("password", [""])[0], self.store.config("password_hash") or ""
        )
        code = form.get("code", [""])[0]
        encrypted_totp = self.store.config("totp_secret")
        try:
            totp_secret = "" if encrypted_totp is None else self.box.open(encrypted_totp).decode()
        except ValueError:
            totp_secret = ""
        second_factor = verify_totp(totp_secret, code, now) if totp_secret else False
        recovery = tuple(json.loads(self.store.config("recovery_hashes") or "[]"))
        recovered, remaining = consume_recovery_code(code, recovery)
        valid &= second_factor or recovered
        if not valid:
            self.store.login_failed(identity, now)
            self.store.audit("login_failed", username or "unknown")
            return self._respond(
                start,
                "401 Unauthorized",
                _layout(
                    "Login failed",
                    "<h1>Login failed</h1><p>Check credentials or wait before retrying.</p>",
                ),
            )
        if recovered:
            self.store.set_config("recovery_hashes", json.dumps(remaining))
            self.store.audit("recovery_code_consumed", username)
        self.store.login_succeeded(identity)
        token, _ = self.store.create_session(now)
        self.store.audit("login", username)
        return self._redirect(start, "/", token=token)

    def _risk_reauthenticated(self, form: dict[str, list[str]]) -> bool:
        password_ok = verify_password(
            form.get("password", [""])[0], self.store.config("password_hash") or ""
        )
        sealed = self.store.config("totp_secret")
        if not password_ok or sealed is None:
            return False
        try:
            secret = self.box.open(sealed).decode()
        except (SecurityError, UnicodeDecodeError):
            return False
        return verify_totp(secret, form.get("totp", [""])[0])

    @staticmethod
    def _overview(
        status: str,
        data: dict[str, Any],
        universe: dict[str, Any],
        realtime: dict[str, Any],
        external: dict[str, int],
        global_state: GlobalProductState,
        opportunities: dict[str, Any],
    ) -> str:
        positions = data.get("positions", [])
        orders = data.get("orders", [])
        fills = data.get("fills", [])
        settlements = data.get("settlements", [])
        cards = (
            ("Account equity", dollars(data.get("portfolio_value")), "Reconciled account value"),
            ("Available cash", dollars(data.get("cash")), "Read-only account balance"),
            ("Protected reserve", "$700.00", "Immutable policy; not allocated by this UI"),
            ("Active allocation", "$0.00", "Production execution is unavailable"),
            ("Open risk", "Unavailable", "M13 reconciliation not complete"),
            ("Worst-case loss", "Unavailable", "Fails closed until M13"),
        )
        activity = (
            ("Markets", universe["indexed"]),
            ("Research candidates", opportunities["research_candidates"]),
            ("Positions", len(positions)),
            ("Orders", len(orders)),
            ("Recent fills", len(fills)),
            ("Settlements", len(settlements)),
            ("Errors / gaps", realtime["unresolved_gaps"]),
        )
        blockers = [
            "Production mutation capability is absent.",
            "M13 deterministic portfolio risk is not complete.",
            "Real settled forecast and execution evidence is insufficient.",
        ]
        if realtime["unresolved_gaps"]:
            blockers.append(f"{realtime['unresolved_gaps']} market-data gap(s) remain unresolved.")
        if external["connector_failures"]:
            blockers.append(
                f"{external['connector_failures']} external connector(s) need attention."
            )
        blocker_items = "".join(f"<li>{html.escape(item)}</li>" for item in blockers)
        return f"<section class=hero><p class=eyebrow>REAL ACCOUNT CONNECTED · READ ONLY</p><h1>Your control center</h1><p class=lede>One place to understand safety, research, account state, and what needs attention.</p><div class=decision-banner><div><small>Can it trade?</small><strong>NO</strong></div><p>Current state: <strong>{html.escape(global_state)}</strong>. This release has no production order capability.</p></div></section><section aria-labelledby=money-heading><h2 id=money-heading>Money and risk</h2><div class=metric-grid>{''.join(f'<article><small>{html.escape(label)}</small><strong>{html.escape(value)}</strong><p>{html.escape(note)}</p></article>' for label, value, note in cards)}</div></section><section class=split><div><h2>What it did</h2><div class=compact-grid>{''.join(f'<article><small>{html.escape(label)}</small><strong>{html.escape(str(value))}</strong></article>' for label, value in activity)}</div></div><aside class=attention><h2>What needs you</h2><ul>{blocker_items}</ul><a class=button href=/risk>Review safety blockers</a></aside></section><section><h2>What changed</h2><div class=empty-state><strong>No approved configuration changes</strong><p>Research proposals and simulated outcomes never alter production behavior.</p><a href=/learning>Review learning governance</a></div></section><section><h2>Connection summary</h2><p>Account: {html.escape(status)} · Universe: {html.escape(str(universe['status']))} · Market data: {html.escape(str(realtime['state']))} · Current/stale books: {realtime['current_books']}/{realtime['stale_books']}.</p></section>"

    @staticmethod
    def _breaking_now(summary: dict[str, int], rows: list[dict[str, Any]]) -> str:
        cards = (
            ("Signals", summary["total"]),
            ("Primary confirmations", summary["primary_confirmations"]),
            ("Unverified leads", summary["unverified"]),
            ("Cross-venue observations", summary["cross_venue"]),
            ("Connector failures", summary["connector_failures"]),
        )
        content = (
            "<p>No external signals. Connectors remain shadow-only.</p>"
            if not rows
            else "".join(
                f"<article class=signal-card><p class=eyebrow>{html.escape(row['source_class'])}</p><h2><a href=/breaking/{html.escape(row['signal_id'])}>{html.escape(row['headline'])}</a></h2><p>{html.escape(row['source_name'])} · {html.escape(row['age_label'])} · {html.escape(row['latency_label'])}</p><p>Verification: {html.escape(row['verification_state'])} · Corroboration: {html.escape(row['corroboration'])}</p><p>Kalshi: {html.escape(row['kalshi_reaction'])} · Polymarket: {html.escape(row['polymarket_reaction'])}</p><p>Noise flags: {html.escape(row['manipulation_flags'] or 'None')}</p><strong>Current action: {html.escape(row['current_action'])} · Trading influence: NONE</strong></article>"
                for row in rows
            )
        )
        return f"<section><p class=eyebrow>SHADOW RESEARCH ONLY</p><h1>Breaking Now</h1><p>Leads increase investigation priority. They never authorize a trade or establish causality.</p><div class=grid>{''.join(f'<article><small>{k}</small><strong>{v}</strong></article>' for k, v in cards)}</div><div class=signal-list>{content}</div></section>"

    @staticmethod
    def _sources(rows: list[dict[str, Any]]) -> str:
        content = (
            "<p>No external sources configured.</p>"
            if not rows
            else "".join(
                f"<article><p class=eyebrow>{html.escape(row['source_class'])}</p><h2>{html.escape(row['source_name'])}</h2><p><span class=badge>{html.escape(row['state'])}</span></p><dl><dt>Freshness / latency</dt><dd>{html.escape(str(row['last_event'] or 'Never'))} · {html.escape(str(row['latency_ms'] if row['latency_ms'] is not None else 'Unavailable'))} ms</dd><dt>Health</dt><dd>Uptime {html.escape(row['uptime'])} · duplicate rate {html.escape(row['duplicate_rate'])} · parse errors {row['parse_errors']}</dd><dt>Coverage</dt><dd>{row['unique_relevant_signals']} unique relevant signals; settled-outcome coverage not yet established</dd><dt>Incremental forecast value</dt><dd>INSUFFICIENT REAL EVIDENCE</dd><dt>Current / previous research weight</dt><dd>Not available · no approved change</dd><dt>Promotion stage</dt><dd>Research only</dd><dt>Monthly cost</dt><dd>{html.escape(row['monthly_cost'])}</dd></dl><strong>Production influence: NONE</strong><p>{html.escape(row['integration_note'])} · {html.escape(str(row['setup_requirement'] or 'No setup required'))}</p></article>"
                for row in rows
            )
        )
        return f"<section><p class=eyebrow>EXTERNAL SOURCE HEALTH</p><h1>Sources</h1><div class=columns>{content}</div></section>"

    @staticmethod
    def _signal_detail(signal_id: str, row: dict[str, Any] | None) -> str:
        if row is None:
            return f"<section><h1>{html.escape(signal_id)}</h1><p>Signal not found.</p></section>"
        return f"<section><p class=eyebrow>SHADOW RESEARCH DATA</p><h1>{html.escape(row['headline'])}</h1><div class=warning>Trading influence: NONE. This is not a trade recommendation.</div><dl><dt>Source lineage</dt><dd>{html.escape(row['source_lineage'])}</dd><dt>Provenance</dt><dd>{html.escape(row['provenance'])}</dd><dt>Timestamps / latency</dt><dd>{html.escape(row['detected_at'])} · {html.escape(row['latency_label'])}</dd><dt>Candidate Kalshi market</dt><dd>{html.escape(str(row['kalshi_market'] or 'Unmatched'))}</dd><dt>Polymarket relationship</dt><dd>{html.escape(row['polymarket_relationship'])}</dd><dt>Verification / corroboration</dt><dd>{html.escape(row['verification_state'])} · {html.escape(row['corroboration'])}</dd><dt>Duplicate chain</dt><dd>{html.escape(row['duplicate_chain'])}</dd><dt>Corrections / deletions</dt><dd>{html.escape(row['correction_state'])}</dd><dt>Manipulation/noise flags</dt><dd>{html.escape(row['manipulation_flags'] or 'None')}</dd><dt>Market reactions</dt><dd>Kalshi: {html.escape(row['kalshi_reaction'])} · Polymarket: {html.escape(row['polymarket_reaction'])}</dd><dt>Current research status</dt><dd>{html.escape(row['current_action'])}</dd></dl><details><summary>Advanced</summary><p>Signal ID: {html.escape(row['signal_id'])}</p><p>Raw payload is retained only according to source policy and is not rendered here.</p></details></section>"

    @staticmethod
    def _markets(
        summary: dict[str, Any],
        rows: list[dict[str, Any]],
        filters: dict[str, str],
        realtime: dict[str, Any],
        semantics: dict[str, int],
    ) -> str:
        cards = (
            ("Markets indexed", summary["indexed"]),
            ("Currently active", summary["active"]),
            ("Provisional", summary["provisional"]),
            ("MVE / unsupported", summary["mve"]),
            ("Data-quality healthy", summary["healthy"]),
            ("Families", summary["families"]),
            ("Semantics validated", semantics["valid"]),
            ("Semantics ambiguous", semantics["ambiguous"]),
            ("Unsupported payout", semantics["unsupported_payout"]),
            ("Missing settlement source", semantics["missing_source"]),
            ("Revalidation required", semantics["revalidation"]),
        )
        items = (
            "<p>No markets match these filters.</p>"
            if not rows
            else "".join(
                f"<article class=market-card><small>{html.escape(row['family'])} · {html.escape(row['series_ticker'])}</small><h2><a href=/markets/{html.escape(row['ticker'])}>{html.escape(row['title'])}</a></h2><p>{html.escape(row['status'])} · closes {html.escape(str(row['closes_at'] or 'unknown'))}</p><p>Executable YES bid/ask: {html.escape(str(row['best_bid'] or '—'))} / {html.escape(str(row['best_ask'] or '—'))}</p><p>Rules/data: {'healthy' if row['quality_healthy'] else 'needs attention'}</p></article>"
                for row in rows
            )
        )
        badge = (
            "REAL-TIME"
            if realtime["state"] == "HEALTHY"
            else f"NOT REAL-TIME: {html.escape(str(realtime['state']))}"
        )
        return f'<section><p class=eyebrow>READ-ONLY DISCOVERY · {badge}</p><h1>Markets</h1><p>Indexed, active, strategy-supported, healthy, and eventually eligible are different states. No opportunities or forecasts are shown. Top-of-book does not imply full depth.</p><div class=grid>{"".join(f"<article><small>{k}</small><strong>{v}</strong></article>" for k, v in cards)}</div><form method=get class=filters><label>Search<input name=q value="{html.escape(filters.get("q", ""))}"></label><label>Family<input name=family value="{html.escape(filters.get("family", ""))}"></label><label>Status<input name=status value="{html.escape(filters.get("status", ""))}"></label><button>Filter</button></form><div class=market-list>{items}</div></section>'

    @staticmethod
    def _market_detail(
        ticker: str,
        spec: dict[str, Any] | None,
        evidence: list[dict[str, Any]] | None = None,
        forecasts: list[dict[str, Any]] | None = None,
    ) -> str:
        evidence = evidence or []
        forecasts = forecasts or []
        if spec is None:
            return f"<section><h1>{html.escape(ticker)}</h1><div class=warning>Settlement semantics are UNPARSED. This market cannot be used by strategy code.</div></section>"
        unsafe = spec["semantic_status"] != "VALID"
        warning = (
            "<div class=warning>Semantic status is unsafe. Later strategy code must fail closed.</div>"
            if unsafe
            else ""
        )
        claims = (
            "<p>No validated evidence bundle is available.</p>"
            if not evidence
            else "".join(
                f"<article><small>{html.escape(row['claim_type'])} · {html.escape(row['validation_state'])}</small><h3>{html.escape(row['claim_text'])}</h3><p>Source: {html.escape(row['source_name'])} · Published: {html.escape(str(row['publication_time'] or 'Unknown'))}</p><blockquote>{html.escape(row['cited_span'])}</blockquote><p>Contract relation: {html.escape(row['contract_relation'])} · Correction: {html.escape(row['correction_state'])} · Contradiction: {html.escape(row['contradiction_state'])}</p><small>Extraction: {html.escape(row['provider_model'])} · Bundle: {html.escape(row['bundle_time'])}</small></article>"
                for row in evidence
            )
        )
        forecast_html = (
            "<p>INSUFFICIENT REAL EVIDENCE</p>"
            if not forecasts
            else "".join(
                f"<article><h3>{html.escape(row['forecast_kind'].replace('_', ' '))}</h3><p>Probability: {html.escape(str(row['probability'] or 'ABSTAIN'))} · interval {html.escape(str(row['lower_probability'] or '—'))} to {html.escape(str(row['upper_probability'] or '—'))}</p><p>Market reference: {html.escape(str(row['market_reference'] or 'unavailable'))}; executable YES bid/ask: {html.escape(str(row['market_bid'] or '—'))}/{html.escape(str(row['market_ask'] or '—'))}</p><p>{html.escape(row['explanation'])}</p></article>"
                for row in forecasts
            )
        )
        return f"<section><p class=eyebrow>HOW THIS MARKET SETTLES · MARKET RESEARCH DETAIL</p><h1>{html.escape(ticker)}</h1>{warning}<div class=decision-banner><div><small>Decision</small><strong>NO ACTION</strong></div><p>No order, proposed size, or maximum loss is available before M13 risk and later execution milestones.</p></div><section><h2>How this market settles</h2><div class=columns><article><h3>YES means</h3><p>{html.escape(spec['yes_proposition'])}</p></article><article><h3>NO means</h3><p>{html.escape(spec['no_proposition'])}</p></article></div><dl><dt>Settlement authority / sources</dt><dd>{html.escape(str(spec['authority'] or 'Unresolved'))} · {html.escape(spec['sources'])}</dd><dt>Measurement / threshold</dt><dd>{html.escape(spec['measured_value'])} · {html.escape(str(spec['threshold'] or 'None'))}</dd><dt>Important date / timezone</dt><dd>{html.escape(str(spec['deadline'] or 'Unresolved'))} · {html.escape(str(spec['timezone'] or 'Unresolved'))}</dd><dt>Revision / correction</dt><dd>{html.escape(str(spec['revision_rules'] or 'Unclear'))} / {html.escape(str(spec['correction_rules'] or 'Unclear'))}</dd><dt>Semantic status</dt><dd>{html.escape(spec['semantic_status'])}</dd><dt>What is unclear</dt><dd>{html.escape(spec['issues'])}</dd></dl></section><section><h2>Forecast and executable market</h2><div class=warning>Market reference, independent model, market-anchored ensemble, and executable bid/ask remain separate. Research only; no trade decision has been made. Production influence: NONE.</div><div class=columns>{forecast_html}</div><p><strong>YES and NO after-cost economics:</strong> available only from a frozen M10 candidate with a fresh full book. No current candidate is inferred from this page.</p></section><section><h2>Supporting and opposing evidence</h2><div class=warning>Validated extraction is research evidence, not probability, edge, or a trade recommendation. Source publication time and correction state remain visible. Production influence: NONE.</div><div class=columns>{claims}</div></section><section><h2>What would change the forecast?</h2><p>A new validated primary-source observation, a corrected rules interpretation, a material source conflict, or a fresh model run may create a new immutable forecast. Existing forecasts are never rewritten.</p></section><details><summary>Forecast and audit history</summary><p>Rules version: {html.escape(spec['rules_version'])}</p><p>Interpretation: {html.escape(spec['interpretation_version'])}</p><p>Semantic hash: {html.escape(spec['semantic_hash'])}</p><p>Book depth and liquidity history require persisted replay fidelity and are not fabricated from top-of-book.</p></details></section>"

    @staticmethod
    def _forecasting(summary: dict[str, Any]) -> str:
        cards = "".join(
            f"<article><h3>{html.escape(row['family'])} — {html.escape(row['horizon'])}</h3><p>{html.escape(row['forecast_kind'].replace('_', ' '))}: {html.escape(str(row['probability'] or 'ABSTAIN'))}</p><p>Market reference: {html.escape(str(row['market_reference'] or 'unavailable'))}</p><p>Calibration: {html.escape(row['calibration_status'])} · Data: {html.escape(row['data_quality'])}</p><small>{'SYNTHETIC FIXTURE' if row['synthetic'] else 'REAL RESEARCH'} · Production influence NONE</small></article>"
            for row in summary["forecasts"]
        )
        return f"<section><p class=eyebrow>RESEARCH / SHADOW ONLY</p><h1>Forecasting and calibration</h1><div class=warning>{html.escape(summary['sample_status'])}. No model edge, profitability, opportunity, or trade claim is made.</div><div class=grid><article><small>Unique settled forecasts</small><strong>{summary['settled_forecasts']}</strong></article><article><small>Unique settled events</small><strong>{summary['settled_events']}</strong></article><article><small>Abstention rate</small><strong>{html.escape(summary['abstention_rate'])}</strong></article><article><small>Calibration</small><strong>{html.escape(summary['calibration_status'])}</strong></article></div>{cards or '<p>No persisted forecasts.</p>'}<details><summary>Advanced evaluation</summary><p>Calibration bins, Brier, log loss, market-relative skill, horizon breakdown and effective samples appear only after immutable settled real evidence exists.</p></details></section>"

    @staticmethod
    def _learning(summary: dict[str, Any]) -> str:
        proposals = (
            "<p>No research changes are proposed.</p>"
            if not summary["proposals"]
            else "".join(
                f"<article><p class=eyebrow>PROPOSED RESEARCH CHANGE</p><h3>{html.escape(row['component_name'])}</h3><p>{html.escape(row['family'])} · {html.escape(row['horizon'])} · {row['settled_events']} unique settled events</p><p>Observed incremental contribution: {html.escape(str(row['observed_contribution'] or 'INCONCLUSIVE'))} · interval {html.escape(row['interval'])}</p><p>Research weight: {html.escape(row['current_weight'])} to {html.escape(row['proposed_weight'])}; maximum weekly change 10 percentage points.</p><p>{html.escape(row['rationale'])}</p><p>Status: {html.escape(row['status'])} · rollback: {html.escape(row['rollback_target'])}</p><strong>{'SYNTHETIC TEST DATA' if row['synthetic'] else 'REAL EVIDENCE'} · PRODUCTION INFLUENCE NONE</strong></article>"
                for row in summary["proposals"]
            )
        )
        families = "".join(
            f"<article><h3>{html.escape(row['family'])}</h3><p>Usable markets: {row['usable_markets']} · settled events: {row['settled_events']}</p><p>Market-relative skill: {html.escape(row['skill_state'])} · completeness: {html.escape(row['data_completeness'])}</p><p>Research cost: {html.escape(row['research_cost'])} · priority: {html.escape(row['research_priority'])}</p><strong>Capital allocation: {html.escape(row['capital_allocation'])}</strong></article>"
            for row in summary["families"]
        )
        return f"<section><p class=eyebrow>RESEARCH GOVERNANCE ONLY</p><h1>What we've learned</h1><div class=warning>{html.escape(summary['state'])}. Progress: {summary['real_settled_events']} / {summary['promotion_minimum']} relevant settled events. Synthetic results test behavior only.</div><h2>Source performance</h2><p>Accuracy, timeliness, originality, redundancy, incremental forecast value and cost remain separate.</p><h2>Model performance</h2><p>Champion/challenger comparisons require identical events, checkpoints and promotion windows.</p><h2>Market families</h2>{families or '<p>No real tournament evidence.</p>'}<h2>Proposed changes</h2>{proposals}<h2>Recent changes</h2><p>Current configuration: {html.escape(str(summary['current_configuration'] or 'None'))}; previous/rollback: {html.escape(str(summary['previous_configuration'] or 'None'))}.</p><strong>PRODUCTION INFLUENCE: {html.escape(summary['production_influence'])}</strong></section>"

    @staticmethod
    def _opportunities(summary: dict[str, Any]) -> str:
        cards = "".join(
            f"<article><p class=eyebrow>{html.escape(row['data_mode'])}</p><h2>{html.escape(row['market_ticker'])} — {html.escape(row['outcome_side'])} ECONOMICS</h2><p>Status: <strong>{html.escape(row['decision_state'])}</strong></p><dl><dt>Fair probability / interval</dt><dd>{html.escape(row['fair_probability'])} · {html.escape(row['lower_probability'])} to {html.escape(row['upper_probability'])}</dd><dt>Executable price</dt><dd>{html.escape(row['executable_price'])}</dd><dt>Raw difference</dt><dd>{html.escape(row['raw_difference'])}</dd><dt>Expected fees</dt><dd>{html.escape(row['expected_fee'])}</dd><dt>Current-book slippage</dt><dd>{html.escape(row['expected_slippage'])}</dd><dt>Conservative after-cost value</dt><dd>{html.escape(row['conservative_value'])}</dd><dt>Required research threshold</dt><dd>{html.escape(row['required_threshold'])}</dd><dt>Liquidity / age</dt><dd>{html.escape(row['liquidity'])} · {html.escape(row['age'])}</dd><dt>Why not a candidate?</dt><dd>{html.escape(row['rejection_reasons'] or 'No deterministic rejection')}</dd></dl><strong>PRODUCTION INFLUENCE: {html.escape(row['production_influence'])}</strong></article>"
            for row in summary["candidates"]
        )
        return f"<section><p class=eyebrow>AFTER-COST RESEARCH ONLY</p><h1>Opportunity research</h1><div class=warning>INSUFFICIENT REAL FORECAST EVIDENCE. Expected value is a frozen model estimate, not realized profit. No trade has been authorized.</div><div class=grid><article><small>Evaluated</small><strong>{summary['evaluated']}</strong></article><article><small>Rejected / watches</small><strong>{summary['rejected']} / {summary['watches']}</strong></article><article><small>Research candidates</small><strong>{summary['research_candidates']}</strong></article><article><small>Stale</small><strong>{summary['stale_candidates']}</strong></article></div>{cards or '<p>No persisted live research candidates.</p>'}<details><summary>System assumptions</summary><p>Worker: {html.escape(summary['worker_state'])}; fee: {html.escape(summary['fee_version'])} / {html.escape(summary['fee_verification'])}; slippage: {html.escape(summary['slippage_version'])}; fill quality: {html.escape(summary['fill_quality'])}; cross venue: {html.escape(summary['cross_venue_state'])}.</p></details></section>"

    @staticmethod
    def _backtests(summary: dict[str, Any]) -> str:
        cards = "".join(
            f"<article><p class=eyebrow>{html.escape(row['data_mode'])}</p><h2>{html.escape(row['strategy'])} · {html.escape(row['family'])}</h2><p>Unique settled events: {row['settled_events']} · attempts: {row['attempts']} · fill rate: {html.escape(row['fill_rate'])} · partial fill rate: {html.escape(row['partial_fill_rate'])}</p><p>Average simulated slippage: {html.escape(row['average_slippage'])} · fee: {html.escape(row['average_fee'])} · maker markout: {html.escape(row['adverse_markout'])}</p><p>Simulated net P&amp;L: {html.escape(row['net_pnl'])} · return on committed capital: {html.escape(row['return_on_capital'])} · max drawdown: {html.escape(row['max_drawdown'])}</p><div class=columns><div><strong>OPTIMISTIC</strong><p>{html.escape(row['optimistic_result'])}</p></div><div><strong>BASE</strong><p>{html.escape(row['base_result'])}</p></div><div><strong>ADVERSE</strong><p>{html.escape(row['adverse_result'])}</p></div></div><p>Research gate: <strong>{html.escape(row['advancement_status'])}</strong></p><p>{html.escape(row['failure_reason'])}</p><details><summary>Execution path and assumptions</summary><p>Frozen M10 economics: {html.escape(row['candidate_economics'])}. Arrival: {html.escape(row['arrival_time'])}. Arrival book: {html.escape(row['arrival_book'])}. Hypothetical order: {html.escape(row['hypothetical_order'])}. Queue: {html.escape(row['queue_assumption'])}. Fill path: {html.escape(row['fill_path'])}. Fees/markouts/settlement: {html.escape(row['result_lineage'])}.</p></details><strong>PRODUCTION INFLUENCE: NONE</strong></article>"
            for row in summary["strategies"]
        )
        return f"<section><p class=eyebrow>HISTORICAL SIMULATION · NOT REAL TRADING RESULTS</p><h1>Execution research backtests</h1><div class=warning>INSUFFICIENT REAL EVIDENCE. Synthetic and historical replay results validate simulation behavior, not strategy profitability. No simulated order can become an exchange order.</div><div class=grid><article><small>Runs</small><strong>{summary['runs']}</strong></article><article><small>Attempts</small><strong>{summary['attempts']}</strong></article><article><small>Unique events</small><strong>{summary['unique_events']}</strong></article><article><small>Real execution observations</small><strong>{html.escape(summary['real_observations'])}</strong></article></div>{cards or '<p>No persisted historical simulation runs.</p>'}<p>Data modes are separate: SYNTHETIC TEST · HISTORICAL REPLAY · REAL PAPER/DEMO · REAL PRODUCTION.</p><strong>PRODUCTION INFLUENCE: {html.escape(summary['production_influence'])}</strong></section>"

    @staticmethod
    def _portfolio(data: dict[str, Any]) -> str:
        groups = []
        for key in ("positions", "orders", "fills", "settlements"):
            rows = data.get(key, [])
            content = (
                "No account activity in this category."
                if not rows
                else "".join(
                    f"<div class=row>{html.escape(str(row.get('ticker', 'Account item')))}</div>"
                    for row in rows
                )
            )
            groups.append(f"<article><h2>{key.title()}</h2>{content}</article>")
        return f"<section><p class=eyebrow>READ-ONLY · RECONCILIATION REQUIRED</p><h1>Portfolio</h1><p>Normalized primary account (subaccount 0). Empty values are not interpreted as zero risk.</p><div class=metric-grid><article><small>Cash</small><strong>{html.escape(dollars(data.get('cash')))}</strong></article><article><small>Equity</small><strong>{html.escape(dollars(data.get('portfolio_value')))}</strong></article><article><small>Unresolved exposure</small><strong>Unavailable</strong><p>M13 reconciliation pending</p></article><article><small>Worst-case outcome</small><strong>Unavailable</strong><p>Not calculated from incomplete state</p></article></div><div class=columns>{''.join(groups)}</div><div class=warning>Fees, settlement dates, event correlation, and worst-case exposure must be reconciled before this page can claim a complete portfolio view.</div></section>"

    @staticmethod
    def _orders_and_trades(data: dict[str, Any]) -> str:
        categories: tuple[tuple[str, list[dict[str, Any]]], ...] = (
            ("Proposed", []),
            ("Approved", []),
            ("Submitted / resting", data.get("orders", [])),
            ("Partial / filled", data.get("fills", [])),
            ("Canceled / rejected", []),
            ("Unknown / reconciliation required", data.get("unknown_orders", [])),
        )
        cards = []
        for title, rows in categories:
            detail = (
                "<div class=empty-state><p>No items in this state.</p></div>"
                if not rows
                else "".join(
                    f"<div class=row><strong>{html.escape(str(row.get('ticker', 'Account item')))}</strong><span>{html.escape(str(row.get('status', title)))}</span></div>"
                    for row in rows
                )
            )
            cards.append(f"<article><h2>{html.escape(title)}</h2>{detail}</article>")
        return f"<section><p class=eyebrow>REAL PRODUCTION · READ-ONLY ACCOUNT HISTORY</p><h1>Orders &amp; Trades</h1><div class=warning>No order can be proposed, submitted, amended, or canceled in real production from this release. Unknown exchange state requires reconciliation; it is never replaced automatically.</div><div class=columns>{''.join(cards)}</div></section><section><p class=eyebrow>MOCK · PAPER · DEMO</p><h2>Execution testing</h2><div class=warning><strong>KALSHI DEMO — MOCK FUNDS · NOT REAL MONEY</strong><p>Demo activity never changes the global real-money state to TRADING.</p></div><div class=columns><article><h3>ORDER STATE UNKNOWN</h3><p>The request may have reached the demo exchange. The system will not resubmit until reconciliation determines what happened.</p><span class=badge>UNKNOWN_RECONCILIATION_REQUIRED</span></article><article><h3>RECOVERY / RECONCILIATION IN PROGRESS</h3><p>Incomplete durable journals block related demo risk until REST orders, fills, positions, and account risk agree.</p><span class=badge>NON-PRODUCTION</span></article></div><p><a class=button href=/demo/setup>Demo credential setup</a></p></section>"

    @staticmethod
    def _demo_setup(csrf: str) -> str:
        return f"""<section class=auth><p class=eyebrow>OPTIONAL · OWNER ONLY</p><h1>Kalshi demo credential</h1><div class=warning><strong>DEMO FUNDS ONLY · NOT PRODUCTION</strong><p>Only the dedicated Kalshi demo origin and subaccount 0 are accepted. Production hosts and production/read credentials are rejected.</p></div><p>Credential enrollment requires password and TOTP reauthentication, an HTTPS PEM upload, exact confirmation, and successful validation against the demo account before encrypted persistence.</p><form method=post enctype=multipart/form-data><input type=hidden name=csrf value="{html.escape(csrf)}"><label>Demo API key ID<input name=key_id autocomplete=off required></label><label>Demo PKCS#8 PEM<input type=file name=pem accept=.pem required></label><label>Password<input type=password name=password required></label><label>TOTP<input name=totp inputmode=numeric required></label><label>Type INSTALL DEMO CREDENTIAL<input name=confirmation required></label><button disabled>Install after live demo validator is configured</button></form><p class=help>No key is requested in chat, shown in support snapshots, or shared with the production-read vault.</p></section>"""

    @staticmethod
    def _risk_and_safety(
        data: dict[str, Any],
        state: GlobalProductState,
        risk: dict[str, object],
        evaluations: list[dict[str, Any]],
        csrf: str,
    ) -> str:
        policy = RiskPolicy()
        kill_labels = _risk_kill_labels(risk)
        limits = (
            ("Protected reserve", dollars(policy.protected_reserve)),
            ("Active-capital allowance", dollars(policy.active_capital)),
            ("Active capital committed", "Unavailable"),
            ("Aggregate open risk", "Unavailable"),
            ("Aggregate risk remaining", "Unavailable"),
            ("Per-market maximum loss", dollars(policy.market_loss_limit)),
            ("Related-event maximum loss", dollars(policy.related_event_risk_limit)),
        )
        halted = bool(risk["global_halt"])
        reset_control = (
            f'<form method=post action=/risk/reset><input type=hidden name=csrf value="{html.escape(csrf)}"><label>Reason<input name=reason required></label><label>Type RESET GLOBAL HALT<input name=confirmation required></label><label>Owner password<input type=password name=password required></label><label>Current TOTP<input name=totp inputmode=numeric required></label><button>Reset global halt</button></form><p class=help>Risk-increasing reset requires strong reauthentication and does not arm trading.</p>'
            if halted
            else "<button disabled aria-describedby=reset-reason>Reset global halt</button><p id=reset-reason class=help>Disabled: global halt is not active.</p>"
        )
        evaluation_cards = (
            "<div class=empty-state><strong>No risk evaluations</strong><p>A fully specified immutable intent and reconciled snapshot are required. Missing data is never shown as zero.</p></div>"
            if not evaluations
            else "".join(
                f"<article><p class=eyebrow>{html.escape(row['data_mode'])}</p><h3>{html.escape(row['market_ticker'])}</h3><dl><dt>Intended maximum loss</dt><dd>{html.escape(dollars(row['intended_maximum_loss']))}</dd><dt>Existing / projected market risk</dt><dd>{html.escape(dollars(row['existing_market_risk']))} / {html.escape(dollars(row['projected_market_risk']))} of {html.escape(dollars(row['market_limit']))}</dd><dt>Projected related-event risk</dt><dd>{html.escape(dollars(row['projected_event_risk']))} of {html.escape(dollars(row['event_limit']))}</dd><dt>Projected aggregate risk</dt><dd>{html.escape(dollars(row['projected_aggregate_risk']))} of {html.escape(dollars(row['aggregate_limit']))}</dd><dt>Reserve</dt><dd>{html.escape(row['reserve_state'])}</dd><dt>Result</dt><dd><strong>{html.escape(row['result'])}</strong></dd><dt>Why</dt><dd>{html.escape(row['reason_codes'] or 'No rejection reason')}</dd></dl><p>This does not authorize an order.</p></article>"
                for row in evaluations
            )
        )
        return f'<section><p class=eyebrow>PRODUCTION RISK ENGINE · DETERMINISTIC</p><h1>Risk &amp; Safety</h1><div class=decision-banner><div><small>Risk state</small><strong>{html.escape(state)}</strong></div><p>The production execution architecture is implemented but DISARMED. No production-write credential is installed. A risk pass means PASS_NEXT_GATE only.</p></div><div class=metric-grid>{"".join(f"<article><small>{html.escape(label)}</small><strong>{html.escape(value)}</strong></article>" for label, value in limits)}</div><section><h2>Loss windows</h2><div class=metric-grid><article><small>Daily loss / allowance remaining</small><strong>Unavailable / Unavailable</strong><p>Stop: {html.escape(dollars(policy.daily_loss_stop))}</p></article><article><small>Weekly loss / allowance remaining</small><strong>Unavailable / Unavailable</strong><p>Stop: {html.escape(dollars(policy.weekly_loss_stop))}; review required {risk["weekly_review_required"]}</p></article><article><small>Monthly loss / allowance remaining</small><strong>Unavailable / Unavailable</strong><p>Stop: {html.escape(dollars(policy.monthly_loss_stop))}; review required {risk["monthly_review_required"]}</p></article><article><small>Experiment equity / high-water mark</small><strong>Unavailable / Unavailable</strong><p>Drawdown and remaining allowance unavailable; halt at {html.escape(dollars(policy.total_drawdown_stop))}; halt required {risk["experiment_halt_required"]}.</p></article></div></section><div class=columns><article><h2>Account safety</h2><dl><dt>Policy version</dt><dd>{html.escape(policy.version)} · {html.escape(policy.content_hash[:12])}</dd><dt>Reconciliation</dt><dd>{"Account snapshot present; real risk reconciliation NOT VERIFIED" if data else "NOT VERIFIED — no current reconciled snapshot"}</dd><dt>Unknown orders / positions</dt><dd>Unavailable / Unavailable</dd><dt>Reservations</dt><dd>{risk["active_reservations"]} active; aggregate {html.escape(dollars(risk["reserved_aggregate_risk"]))}</dd><dt>Compliance hold</dt><dd>{html.escape(str(risk["compliance_state"]))} · {html.escape(str(risk["compliance_reason"] or "No reason recorded"))}</dd><dt>Global halt</dt><dd>{"ACTIVE" if halted else "CLEAR"} · {html.escape(str(risk["global_halt_reason"] or "No reason recorded"))}</dd></dl></article><article><h2>Kill categories</h2><dl><dt>Strategy kill</dt><dd>{html.escape(kill_labels.get("STRATEGY", "NOT VERIFIED"))}</dd><dt>Data kill</dt><dd>{html.escape(kill_labels.get("DATA", "NOT VERIFIED"))}</dd><dt>Portfolio kill</dt><dd>{html.escape(kill_labels.get("PORTFOLIO", "NOT VERIFIED"))}</dd><dt>Credential kill</dt><dd>{html.escape(kill_labels.get("CREDENTIAL", "NOT VERIFIED"))}</dd></dl><p>No model or LLM can override a kill or financial limit.</p></article><article><h2>Halt new risk</h2><p>Blocks future internal risk authorizations immediately. It does not cancel exchange orders in this release.</p><form method=post action=/risk/halt><input type=hidden name=csrf value="{html.escape(csrf)}"><label>Reason<input name=reason required></label><label>Type HALT NEW RISK<input name=confirmation required></label><label>Owner password<input type=password name=password required></label><label>Current TOTP<input name=totp inputmode=numeric required></label><button>Halt new risk</button></form></article><article><h2>Production activation</h2><strong>UNAVAILABLE</strong><p>There is no activation control in this release. Production remains DISARMED and requires a separate future human-governed deployment workflow.</p>{reset_control}</article></div><section><h2>Risk evaluation history</h2><div class=columns>{evaluation_cards}</div></section><div class=warning>New risk authorization is blocked whenever state is unknown. M13 cannot cancel an external Kalshi order and does not claim that cancellation occurred.</div></section>'

    @staticmethod
    def _advanced(
        forecasts: dict[str, Any],
        backtests: dict[str, Any],
        learning: dict[str, Any],
    ) -> str:
        return f"<section><p class=eyebrow>ADVANCED RESEARCH DIAGNOSTICS</p><h1>Advanced</h1><p>Detailed research artifacts live here so the primary owner workflow remains plain-language first.</p><div class=columns><article><h2>Forecasting &amp; calibration</h2><p>{forecasts['settled_events']} settled events · {html.escape(forecasts['calibration_status'])} calibration.</p><a class=button href=/forecasting>Open forecasting</a></article><article><h2>Execution backtests</h2><p>{backtests['runs']} runs · {backtests['attempts']} attempts · real observations {html.escape(backtests['real_observations'])}.</p><a class=button href=/backtests>Open backtests</a></article><article><h2>Learning configuration</h2><p>{learning['real_settled_events']} / {learning['promotion_minimum']} relevant settled events.</p><a class=button href=/learning>Open governance</a></article><article><h2>Raw data</h2><p>Raw JSON is available only through sanitized downloads, never in primary views.</p><a class=button href=/reports>Open reports</a></article></div></section>"

    @staticmethod
    def _supervised_canary() -> str:
        gates = (
            ("M13 deterministic risk engine", "VERIFIED"),
            ("M14 live demo acceptance", "NOT VERIFIED"),
            ("M15 production path", "VERIFIED"),
            ("Production read connection", "NOT VERIFIED"),
            ("Real account reconciliation", "NOT VERIFIED"),
            ("Official API compatibility", "NOT VERIFIED"),
            ("PostgreSQL live concurrency", "NOT VERIFIED"),
            ("Signer runtime isolation", "NOT VERIFIED"),
            ("Production write credential", "NOT VERIFIED"),
            ("Exchange and account freshness", "BLOCKED"),
            ("Human approval capability", "PENDING"),
        )
        rows = "".join(
            f"<div class=row><strong>{html.escape(name)}</strong><span>{state}</span></div>"
            for name, state in gates
        )
        return f"""<section><p class=eyebrow>REAL PRODUCTION · REAL MONEY</p><h1>Supervised canary</h1><div class=warning><strong>NOT AVAILABLE</strong><p>Production write credential has not been installed. This is expected during offline implementation.</p></div><p>The workflow authorizes exactly one immutable one-contract order only after every live gate and a separate future owner instruction. It never turns trading on.</p><h2>Readiness</h2>{rows}<section><h2>ONE-CONTRACT REAL-MONEY CANARY</h2><p>Market, resolution rule, BUY YES / BUY NO, exact $0.xxxx limit, quantity 1.00, maximum fee/loss, forecast, after-cost evidence, uncertainty, risk, reserve, reconciliation, exchange status, and freshness will be frozen into one preview.</p><p><strong>APPROVE THIS ONE-CONTRACT CANARY</strong> requires password, TOTP, CSRF, a recent session, and a fresh M13 authorization. No approval control is available while live gates are missing.</p></section><div class=warning><strong>PRODUCTION ORDER STATE UNKNOWN</strong><p>If a future request may have reached Kalshi, no second order will be submitted and new risk remains blocked during reconciliation.</p></div><div class=actions><a class=button href=/risk>Return to Risk &amp; Safety</a><a class=button href=/autonomy>Review bounded-autonomy governance</a></div></section>"""

    @staticmethod
    def _bounded_autonomy() -> str:
        gates = (
            "Supervised production canary completed",
            "Real account reconciliation current",
            "No unknown orders or positions",
            "Strategy evidence sufficient",
            "Drawdown and concentration acceptable",
            "Signer runtime and PostgreSQL concurrency live verified",
            "Official API compatibility and production reads current",
            "Compliance, global halt, and kill states clear",
            "Independent human governance approval",
        )
        rows = "".join(
            f"<div class=row><span>{html.escape(gate)}</span><strong>NOT VERIFIED</strong></div>"
            for gate in gates
        )
        return f"""<section><p class=eyebrow>M17 · NON-ACTIVE ARCHITECTURE</p><h1>Bounded autonomy</h1><div class=warning><strong>AUTONOMY OFF</strong><p>This release contains governance and evidence-state architecture only. It cannot activate production or authorize an order.</p></div><div class=metric-grid><article><small>Autonomy</small><strong>OFF</strong></article><article><small>Production state</small><strong>DISARMED</strong></article><article><small>Production write credential</small><strong>NONE</strong></article><article><small>Production influence</small><strong>NONE</strong></article></div><h2>Promotion evidence</h2>{rows}<section><h2>Structural ceiling</h2><p>At most one 1.00-contract order in one market would remain subject to exact human approval. Automatic scaling, activation, and execution are structurally unavailable in M17.</p><p>Even complete evidence cannot turn autonomy on. A later milestone and separate human governance decision would be required.</p></section><p><a class=button href=/canary>Review supervised canary readiness</a></p></section>"""

    @staticmethod
    def _system(
        state: Any,
        data: dict[str, Any],
        universe: dict[str, Any],
        realtime: dict[str, Any],
        historical: dict[str, Any],
        llm: dict[str, Any],
    ) -> str:
        connection = f"<section><p class=eyebrow>OPERATIONS &amp; COMPATIBILITY</p><h1>System</h1><div class=warning><strong>LIVE OPERATIONS NOT VERIFIED</strong><p>Missing or stale operational evidence fails closed. Production remains DISARMED and autonomy remains OFF.</p></div><div class=columns><article><h2>Release</h2><dl><dt>Git SHA</dt><dd>{html.escape(str(data.get('git_sha', 'Not recorded')))}</dd><dt>API compatibility</dt><dd>{html.escape(str(data.get('api_compatibility', 'NOT VERIFIED')))}</dd><dt>Spec checksum</dt><dd>{html.escape(str(data.get('spec_checksum', 'Not recorded')))}</dd><dt>Database</dt><dd>{html.escape(str(data.get('database_health', 'Local state available')))}</dd></dl></article><article><h2>Connection health</h2><dl><dt>Account gateway</dt><dd>{html.escape(state.status)}</dd><dt>Credential</dt><dd>Exactly read-only; required for live WebSocket handshake. No write key.</dd><dt>API tier</dt><dd>{html.escape(str(data.get('api_tier', 'Unknown')))}</dd><dt>Universe worker</dt><dd>{html.escape(str(universe['status']))}</dd><dt>WebSocket</dt><dd>{html.escape(str(realtime['state']))}</dd><dt>Gaps / unresolved</dt><dd>{realtime['gap_count']} / {realtime['unresolved_gaps']}</dd></dl></article><article><h2>Continuity</h2><dl><dt>Raw archive</dt><dd>{html.escape(str(realtime['archive_state']))}</dd><dt>Historical cutoff</dt><dd>{html.escape(str(universe['historical_cutoff'] or 'Unknown'))}</dd><dt>Backup</dt><dd>{html.escape(str(data.get('backup_status', 'NOT VERIFIED')))}</dd><dt>Restore drill</dt><dd>{html.escape(str(data.get('restore_status', 'NOT VERIFIED')))}</dd></dl></article><article><h2>Workers &amp; budgets</h2><dl><dt>Queue depth</dt><dd>{realtime['queue_depth']}</dd><dt>Processing lag</dt><dd>{realtime['processing_lag_ms']} ms</dd><dt>Rate budget</dt><dd>{html.escape(str(data.get('rate_budget', 'Not reported')))}</dd><dt>Monthly operations target / hard cap</dt><dd>$25.00 / $50.00 · observed NOT VERIFIED</dd><dt>Last successful sync</dt><dd>{html.escape(str(state.last_success or 'Never'))}</dd></dl></article></div></section>"
        research = f"<section><h2>Research data</h2><p>Historical coverage: <strong>{html.escape(str(historical['coverage']))}</strong></p><p>Market / trade / account coverage: {html.escape(str(historical['market_coverage']))} / {html.escape(str(historical['trade_coverage']))} / {html.escape(str(historical['account_coverage']))}.</p><p>Replay datasets / known gaps: {historical['dataset_count']} / {historical['gap_count']}.</p><p>Availability / rules / fee reconstruction: {html.escape(str(historical['availability_quality']))} / {html.escape(str(historical['rules_quality']))} / {html.escape(str(historical['fee_quality']))}.</p><p>Partial coverage is excluded when a strategy requires stronger point-in-time fidelity. Candle data is never presented as order-book replay.</p></section>"
        evidence = f"<section><h2>Document evidence</h2><p>Provider state: {html.escape(llm['state'])} · {html.escape(llm['provider'])} / {html.escape(llm['model'])}</p><p>Prompt: {html.escape(llm['prompt_version'])} · Requests: {llm['request_count']} · Schema/citation failures: {llm['schema_failures']} / {llm['citation_failures']} · Abstentions: {llm['abstentions']}</p><p>Tokens input/output: {llm['input_tokens']} / {llm['output_tokens']} · Estimated monthly cost: {html.escape(str(llm['estimated_monthly_cost'] or 'UNKNOWN'))}</p><p>Eval: {html.escape(llm['eval_status'])}. No provider key, private account data, probability, or trading authority is displayed or sent.</p></section>"
        production = """<section><p class=eyebrow>PRODUCTION EXECUTION SECURITY</p><h2>Production write path</h2><dl><dt>Execution path</dt><dd>IMPLEMENTED / OFFLINE VERIFIED</dd><dt>Production write credential</dt><dd>NOT INSTALLED — it is not needed yet.</dd><dt>Production signer</dt><dd>DISARMED</dd><dt>Production orders</dt><dd>DISABLED</dd><dt>Real-money order executed</dt><dd>NO</dd></dl><div class=warning>The technical sign-and-send path exists, but it cannot transmit a production order until later supervised activation requirements are satisfied.</div></section>"""
        return connection + production + research + evidence

    @staticmethod
    def _stale(last_success: str | None) -> bool:
        if last_success is None:
            return True
        try:
            return bool(time.time() - datetime.fromisoformat(last_success).timestamp() > 600)
        except ValueError:
            return True

    @staticmethod
    def _form(environ: dict[str, Any]) -> dict[str, list[str]]:
        length = min(int(environ.get("CONTENT_LENGTH") or 0), 65536)
        return parse_qs(environ["wsgi.input"].read(length).decode())

    @staticmethod
    def _multipart(environ: dict[str, Any]) -> dict[str, bytes]:
        length = min(int(environ.get("CONTENT_LENGTH") or 0), 256_000)
        content_type = str(environ.get("CONTENT_TYPE", ""))
        if not content_type.startswith("multipart/form-data;"):
            raise ValueError("multipart upload required")
        raw = environ["wsgi.input"].read(length)
        message = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + raw
        )
        result: dict[str, bytes] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if isinstance(name, str):
                payload = part.get_payload(decode=True)
                if not isinstance(payload, bytes):
                    raise ValueError("invalid multipart field")
                result[name] = payload
        return result

    @staticmethod
    def _cookie(environ: dict[str, Any], name: str) -> str:
        jar = cookies.SimpleCookie()
        jar.load(str(environ.get("HTTP_COOKIE", "")))
        return "" if name not in jar else jar[name].value

    @staticmethod
    def _respond(
        start: StartResponse,
        status: str,
        body: bytes,
        content_type: str = "text/html; charset=utf-8",
        download: str | None = None,
    ) -> list[bytes]:
        headers = [("Content-Type", content_type), ("Cache-Control", "no-store"), *SECURITY_HEADERS]
        if download:
            headers.append(("Content-Disposition", f'attachment; filename="{download}"'))
        start(status, headers)
        return [body]

    @staticmethod
    def _redirect(
        start: StartResponse, location: str, token: str | None = None, expire: bool = False
    ) -> list[bytes]:
        headers = [("Location", location), *SECURITY_HEADERS]
        if token:
            headers.append(
                (
                    "Set-Cookie",
                    f"session={token}; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600",
                )
            )
        if expire:
            headers.append(
                ("Set-Cookie", "session=; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
            )
        start("303 See Other", headers)
        return [b""]


CSS = """*:where(:not(dialog)){box-sizing:border-box}html{color-scheme:light}body{margin:0;background:#f5f6f3;color:#142019;font:16px/1.55 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:#155d3a;text-underline-offset:.18em}a:hover{text-decoration-thickness:2px}:focus-visible{outline:3px solid #d89400;outline-offset:3px}.skip-link{position:fixed;left:1rem;top:-5rem;background:#fff;color:#10251a;padding:.75rem 1rem;z-index:20;border-radius:.5rem}.skip-link:focus{top:1rem}header{padding:1rem max(1rem,calc((100vw - 1440px)/2));background:#10251a;color:#fff;display:flex;align-items:center;gap:2rem}.brand{font-size:1.05rem;font-weight:800;color:#fff;text-decoration:none;white-space:nowrap}nav{display:flex;gap:.25rem;flex-wrap:wrap}nav a{color:#dce8e0;text-decoration:none;padding:.65rem .75rem;border-radius:.5rem;min-height:44px}nav a:hover,nav a[aria-current=page]{background:#244636;color:#fff}.global-state{display:flex;align-items:center;justify-content:center;gap:1rem;padding:.65rem 1rem;background:#e7efe9;border-bottom:1px solid #c9d8cd;font-size:.9rem}.global-state strong{padding:.2rem .55rem;border-radius:999px;background:#204d36;color:#fff}.global-state.needs-attention,.global-state.halted{background:#fff0d2;border-color:#e5bb68}.global-state.needs-attention strong,.global-state.halted strong{background:#8b4800}main{max-width:1280px;margin:auto;padding:clamp(1.25rem,4vw,3.5rem);min-height:70vh}section+section{margin-top:3rem}h1,h2,h3{line-height:1.15;letter-spacing:-.02em}h1{font-size:clamp(2.25rem,6vw,4.75rem);max-width:18ch;margin:.3rem 0 1rem}h2{font-size:clamp(1.4rem,3vw,2rem)}p{max-width:74ch}.hero{padding:1.5rem 0}.lede{font-size:clamp(1.1rem,2vw,1.35rem);color:#3d5045}.eyebrow{color:#206b47;font-size:.78rem;font-weight:850;letter-spacing:.12em}.grid,.metric-grid,.compact-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:1rem}.metric-grid article,.grid article,.columns article,.compact-grid article,.market-card,.signal-card,section.auth{background:#fff;border:1px solid #d9e0da;border-radius:14px;padding:1.2rem;box-shadow:0 2px 10px #10251a0a;overflow-wrap:anywhere}.metric-grid strong,.grid strong,.compact-grid strong{display:block;font-size:clamp(1.35rem,3vw,1.85rem);margin-top:.35rem;font-variant-numeric:tabular-nums}.metric-grid p{font-size:.88rem;color:#56645c;margin-bottom:0}.compact-grid{grid-template-columns:repeat(auto-fit,minmax(120px,1fr))}.compact-grid article{padding:1rem}.columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}.split{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:2rem}.attention{background:#fff8e8;border:1px solid #e4c98d;border-radius:14px;padding:1.25rem}.decision-banner{display:flex;align-items:center;gap:1.5rem;background:#edf3ef;border-left:6px solid #236b48;padding:1rem 1.25rem;border-radius:.25rem .75rem .75rem .25rem}.decision-banner div{min-width:110px}.decision-banner strong{display:block;font-size:1.7rem}.warning{background:#fff3d8;border-left:5px solid #a75b00;padding:1rem;border-radius:.25rem .65rem .65rem .25rem;color:#4c2b00}.empty-state{background:#f8faf8;border:1px dashed #aab8ae;border-radius:.75rem;padding:1.1rem;color:#445249}.row{padding:.75rem 0;border-top:1px solid #dce2dd;display:flex;justify-content:space-between;gap:1rem}.auth{max-width:560px;margin:auto}label{display:grid;gap:.4rem;margin:1rem 0}.help,small{color:#59675e}input,button,.button{min-height:44px;padding:.72rem .9rem;border-radius:8px;border:1px solid #829187;font:inherit}button,.button{background:#17613d;color:#fff;text-decoration:none;cursor:pointer;display:inline-flex;align-items:center;justify-content:center}button:disabled{background:#d8ddda;color:#505c55;cursor:not-allowed}.actions{display:flex;gap:.75rem;flex-wrap:wrap}dt{font-weight:750;margin-top:1rem}dd{margin-left:0}.badge{display:inline-flex;background:#e8eeea;padding:.25rem .6rem;border-radius:999px;font-size:.8rem;font-weight:750}details{margin-top:1rem;border-top:1px solid #d9e0da;padding-top:1rem}summary{cursor:pointer;min-height:44px}footer{padding:1.25rem max(1rem,calc((100vw - 1440px)/2));background:#10251a;color:#dce8e0;display:flex;gap:1.25rem;flex-wrap:wrap}footer strong{color:#fff}@media(max-width:900px){header{align-items:flex-start;flex-direction:column;gap:.75rem}nav{max-height:9.5rem;overflow:auto}.split{grid-template-columns:1fr}.global-state{justify-content:flex-start;overflow-x:auto}.global-state span,.global-state strong{white-space:nowrap}}@media(max-width:650px){main{padding:1rem}.columns{grid-template-columns:1fr}.decision-banner{align-items:flex-start;flex-direction:column}.row{display:block}footer{display:grid;gap:.5rem}h1{font-size:2.45rem}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}"""
