from __future__ import annotations

from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, body: str, status: int = 200) -> None:
    payload = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
    handler.send_response(HTTPStatus.SEE_OTHER)
    handler.send_header("Location", location)
    handler.end_headers()


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0:
        return {}
    body = handler.rfile.read(content_length)
    if not body:
        return {}
    return json.loads(body.decode("utf-8"))


def _read_form_body(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    if content_length <= 0:
        return {}
    body = handler.rfile.read(content_length)
    if not body:
        return {}
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _parse_gate_context(raw_context: Any) -> dict[str, Any]:
    if isinstance(raw_context, dict):
        return dict(raw_context)
    if isinstance(raw_context, str) and raw_context.strip():
        try:
            payload = json.loads(raw_context)
        except json.JSONDecodeError:
            return {}
        if isinstance(payload, dict):
            return payload
    return {}


def _render_option_buttons(gate_id: str, brief: dict[str, Any]) -> str:
    options = brief.get("options", [])
    if not isinstance(options, list) or not options:
        return ""
    forms: list[str] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or option.get("value") or "").strip()
        value = str(option.get("value") or option.get("label") or "").strip()
        description = str(option.get("description") or "").strip()
        if not label or not value:
            continue
        forms.append(
            (
                '<form method="post" action="/human/reply" class="choice-form">'
                f'<input type="hidden" name="gate_id" value="{escape(gate_id)}">'
                '<input type="hidden" name="sender" value="human">'
                f'<input type="hidden" name="message" value="{escape(value)}">'
                f'<button type="submit">{escape(label)}</button>'
                f'<span>{escape(description)}</span>'
                "</form>"
            )
        )
    return "".join(forms)


def _render_human_page(server: "CodexAppServer", notice: str = "", error: str = "") -> str:
    runtime = server.bridge.snapshot()
    communication = server.communication_store.snapshot()
    gates = communication.get("gates", [])
    open_gates = [gate for gate in gates if isinstance(gate, dict) and gate.get("status") == "open"]
    gate = open_gates[0] if open_gates else None
    mission = runtime.get("mission", {}) if isinstance(runtime, dict) else {}
    state = runtime.get("state", {}) if isinstance(runtime, dict) else {}
    runtime_status = state.get("status") or mission.get("status") or "unknown"
    refresh = '<meta http-equiv="refresh" content="3">' if gate or runtime_status in {"waiting_human", "running"} else ""
    gate_html = ""
    if gate:
        brief = _parse_gate_context(gate.get("context"))
        question = str(brief.get("question") or gate.get("title") or "").strip()
        reason = str(brief.get("why_not_auto_answered") or "").strip()
        recommendation = str(brief.get("supervisor_recommendation") or "").strip()
        reply_shape = str(brief.get("required_reply_shape") or "").strip()
        prompt = str(gate.get("prompt") or "").strip()
        gate_html = f"""
        <section class="gate">
          <div class="eyebrow">Decision Needed</div>
          <h2>{escape(str(gate.get("title") or "Pending decision"))}</h2>
          <p class="question">{escape(question)}</p>
          <div class="meta-row">
            <span>Status: {escape(str(gate.get("status") or "open"))}</span>
            <span>Severity: {escape(str(gate.get("severity") or "decision_gate"))}</span>
            <span>Gate ID: {escape(str(gate.get("id") or ""))}</span>
          </div>
          <pre>{escape(prompt)}</pre>
          {"<p><strong>Why you are being asked:</strong> " + escape(reason) + "</p>" if reason else ""}
          {"<p><strong>Supervisor recommendation:</strong> " + escape(recommendation) + "</p>" if recommendation else ""}
          {"<p><strong>Reply shape:</strong> " + escape(reply_shape) + "</p>" if reply_shape else ""}
          <div class="choices">{_render_option_buttons(str(gate.get("id") or ""), brief)}</div>
          <form method="post" action="/human/reply" class="reply-form">
            <input type="hidden" name="gate_id" value="{escape(str(gate.get("id") or ""))}">
            <input type="hidden" name="sender" value="human">
            <label for="message">Your reply</label>
            <textarea id="message" name="message" rows="6" placeholder="Type the decision, selected option, or concrete constraint here." required></textarea>
            <button type="submit">Send reply</button>
          </form>
        </section>
        """
    else:
        gate_html = """
        <section class="gate idle">
          <div class="eyebrow">No Pending Decision</div>
          <h2>No human reply is needed right now.</h2>
          <p>The harness will keep going on its own until it either finishes or needs a real decision.</p>
        </section>
        """

    notice_html = f'<div class="notice ok">{escape(notice)}</div>' if notice else ""
    error_html = f'<div class="notice error">{escape(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    {refresh}
    <title>Harness Human Reply</title>
    <style>
      :root {{
        color-scheme: light;
        --bg: #f6f1e8;
        --panel: #fffaf2;
        --ink: #1f1a17;
        --muted: #6d6258;
        --line: #d8c9b7;
        --accent: #0d6b5f;
        --accent-soft: #dff1ed;
        --error: #8c2f20;
        --error-soft: #f9e3df;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
        background:
          radial-gradient(circle at top right, rgba(13,107,95,0.10), transparent 28rem),
          linear-gradient(180deg, #fbf7ef 0%, var(--bg) 100%);
        color: var(--ink);
      }}
      main {{
        width: min(56rem, calc(100% - 2rem));
        margin: 2rem auto 3rem;
      }}
      .hero, .gate {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 20px;
        padding: 1.25rem 1.25rem 1.4rem;
        box-shadow: 0 18px 40px rgba(47, 36, 28, 0.08);
      }}
      .hero {{
        margin-bottom: 1rem;
      }}
      .eyebrow {{
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-size: 0.78rem;
        color: var(--accent);
        font-weight: 700;
      }}
      h1, h2 {{
        margin: 0.4rem 0 0.6rem;
        line-height: 1.15;
      }}
      p {{
        margin: 0.5rem 0;
        line-height: 1.55;
      }}
      .meta-row {{
        display: flex;
        flex-wrap: wrap;
        gap: 0.65rem;
        margin: 0.75rem 0 1rem;
        color: var(--muted);
        font-size: 0.95rem;
      }}
      .meta-row span {{
        background: #f5ede1;
        border-radius: 999px;
        padding: 0.3rem 0.65rem;
      }}
      pre {{
        white-space: pre-wrap;
        padding: 0.95rem;
        border-radius: 14px;
        background: #f5ede1;
        border: 1px solid var(--line);
        font: inherit;
        line-height: 1.55;
      }}
      .notice {{
        margin-bottom: 1rem;
        padding: 0.85rem 1rem;
        border-radius: 14px;
        border: 1px solid transparent;
      }}
      .notice.ok {{
        background: var(--accent-soft);
        border-color: #b9ddd7;
      }}
      .notice.error {{
        background: var(--error-soft);
        border-color: #e8b7ad;
        color: var(--error);
      }}
      .choices {{
        display: grid;
        gap: 0.75rem;
        margin: 1rem 0 1.2rem;
      }}
      .choice-form {{
        display: grid;
        gap: 0.35rem;
        padding: 0.85rem 0.95rem;
        border: 1px solid var(--line);
        border-radius: 14px;
        background: #fffdf9;
      }}
      .choice-form button, .reply-form button {{
        justify-self: start;
        appearance: none;
        border: none;
        background: var(--accent);
        color: white;
        border-radius: 999px;
        padding: 0.7rem 1rem;
        font: inherit;
        cursor: pointer;
      }}
      .reply-form {{
        display: grid;
        gap: 0.65rem;
      }}
      label {{
        font-weight: 600;
      }}
      textarea {{
        width: 100%;
        resize: vertical;
        min-height: 10rem;
        padding: 0.9rem 1rem;
        border: 1px solid var(--line);
        border-radius: 14px;
        font: inherit;
        background: #fffdf9;
      }}
      .footer {{
        margin-top: 0.9rem;
        color: var(--muted);
        font-size: 0.95rem;
      }}
    </style>
  </head>
  <body>
    <main>
      {notice_html}
      {error_html}
      <section class="hero">
        <div class="eyebrow">Harness Human Reply</div>
        <h1>{escape(str(mission.get("goal") or "Current harness run"))}</h1>
        <p>This page is the human-facing inbox for the harness. When a real decision is needed, reply here and the blocked agent will continue automatically.</p>
        <div class="meta-row">
          <span>Mission status: {escape(str(mission.get("status") or "unknown"))}</span>
          <span>Runtime status: {escape(str(runtime_status))}</span>
          <span>Round: {escape(str(state.get("current_round") or mission.get("round") or 0))}</span>
          <span>Active agent: {escape(str(state.get("active_agent") or "idle"))}</span>
        </div>
      </section>
      {gate_html}
      <p class="footer">This page refreshes automatically while the harness is active.</p>
    </main>
  </body>
</html>"""

class CodexAppRequestHandler(BaseHTTPRequestHandler):
    server: "CodexAppServer"

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        path = request.path
        query = parse_qs(request.query)
        if path == "/":
            _html_response(
                self,
                _render_human_page(
                    self.server,
                    notice=str(query.get("notice", [""])[-1] or ""),
                    error=str(query.get("error", [""])[-1] or ""),
                ),
            )
            return
        if path == "/human":
            _html_response(
                self,
                _render_human_page(
                    self.server,
                    notice=str(query.get("notice", [""])[-1] or ""),
                    error=str(query.get("error", [""])[-1] or ""),
                ),
            )
            return
        if path == "/health":
            _json_response(self, {"ok": True})
            return
        if path == "/runtime":
            _json_response(
                self,
                {
                    "runtime": self.server.bridge.snapshot(),
                    "communication": self.server.communication_store.snapshot(),
                },
            )
            return
        if path == "/communication/messages":
            _json_response(self, {"messages": self.server.communication_store.list_messages()})
            return
        if path == "/communication/gates":
            _json_response(self, {"gates": self.server.communication_store.list_gates()})
            return
        _json_response(self, {"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        request = urlsplit(self.path)
        path = request.path
        if path == "/human/reply":
            payload = _read_form_body(self)
            gate_id = str(payload.get("gate_id", "")).strip()
            sender = str(payload.get("sender", "human")).strip() or "human"
            message = str(payload.get("message", "")).strip()
            if not gate_id:
                _redirect(self, "/?error=" + quote("Missing gate id"))
                return
            if not message:
                _redirect(self, "/?error=" + quote("Reply cannot be empty"))
                return
            try:
                self.server.communication_store.reply_to_gate(
                    gate_id,
                    sender=sender,
                    body=message,
                )
            except KeyError:
                _redirect(self, "/?error=" + quote("That gate is no longer available"))
                return
            except ValueError as exc:
                _redirect(self, "/?error=" + quote(str(exc)))
                return
            _redirect(self, "/?notice=" + quote("Reply sent. The harness will continue automatically."))
            return

        payload = _read_json_body(self)
        if path == "/communication/messages":
            try:
                message = self.server.communication_store.append_message(
                    sender=str(payload.get("sender", "human")),
                    body=str(payload.get("body", "")),
                    gate_id=payload.get("gate_id"),
                    kind=str(payload.get("kind", "message")),
                )
            except ValueError as exc:
                _json_response(self, {"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            _json_response(self, {"message": message})
            return
        if path == "/communication/gates":
            try:
                gate = self.server.communication_store.open_gate(
                    title=str(payload.get("title", "")),
                    prompt=str(payload.get("prompt", "")),
                    source=str(payload.get("source", "supervisor")),
                    severity=str(payload.get("severity", "decision_gate")),
                    context=payload.get("context"),
                )
            except ValueError as exc:
                _json_response(self, {"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            _json_response(self, {"gate": gate})
            return
        if path.startswith("/communication/gates/") and path.endswith("/reply"):
            gate_id = path[len("/communication/gates/") : -len("/reply")].strip("/")
            try:
                gate = self.server.communication_store.reply_to_gate(
                    gate_id,
                    sender=str(payload.get("sender", "human")),
                    body=str(payload.get("body", "")),
                )
            except KeyError:
                _json_response(self, {"error": "gate not found"}, status=HTTPStatus.NOT_FOUND)
                return
            except ValueError as exc:
                _json_response(self, {"error": str(exc)}, status=HTTPStatus.CONFLICT)
                return
            _json_response(self, {"gate": gate})
            return
        _json_response(self, {"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return


class CodexAppServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], *, bridge: Any, communication_store: Any) -> None:
        self.bridge = bridge
        self.communication_store = communication_store
        super().__init__(server_address, CodexAppRequestHandler)
