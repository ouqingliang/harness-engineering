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
    initial_monitor = {
        "runtime_status": str(runtime_status),
        "mission_status": str(mission.get("status") or "unknown"),
        "round": str(state.get("current_round") or mission.get("round") or 0),
        "active_agent": str(state.get("active_agent") or "idle"),
        "pending_gate_id": str(state.get("pending_gate_id") or ""),
        "gate_id": str(gate.get("id") or "") if isinstance(gate, dict) else "",
        "gate_status": str(gate.get("status") or "") if isinstance(gate, dict) else "",
        "running_agents": runtime.get("running_agents", []) if isinstance(runtime, dict) else [],
        "queued_slices": runtime.get("queued_slices", []) if isinstance(runtime, dict) else [],
        "recent_events": runtime.get("recent_events", []) if isinstance(runtime, dict) else [],
        "agent_statuses": runtime.get("agent_statuses", []) if isinstance(runtime, dict) else [],
    }
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
    <title>Harness Monitor</title>
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
      .monitor {{
        display: grid;
        gap: 0.9rem;
        margin-bottom: 1rem;
      }}
      .monitor-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
        gap: 0.75rem;
      }}
      .monitor-lists {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
        gap: 0.75rem;
      }}
      .agent-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
        gap: 0.75rem;
      }}
      .monitor-card {{
        background: #fffdf9;
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 0.9rem 1rem;
      }}
      .monitor-card strong {{
        display: block;
        font-size: 0.82rem;
        color: var(--muted);
        margin-bottom: 0.3rem;
      }}
      .list-card {{
        background: #fffdf9;
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 0.9rem 1rem;
      }}
      .list-card strong {{
        display: block;
        font-size: 0.82rem;
        color: var(--muted);
        margin-bottom: 0.5rem;
      }}
      .list-card ul {{
        margin: 0;
        padding-left: 1rem;
      }}
      .list-card li {{
        margin: 0.35rem 0;
        color: var(--muted);
        line-height: 1.45;
      }}
      .agent-card {{
        background: #fffdf9;
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 0.9rem 1rem;
      }}
      .agent-card .agent-name {{
        font-weight: 700;
        margin-bottom: 0.2rem;
      }}
      .agent-card .agent-status {{
        color: var(--accent);
        font-size: 0.85rem;
        margin-bottom: 0.45rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
      }}
      .agent-card p {{
        margin: 0.25rem 0;
        color: var(--muted);
      }}
      .agent-state-chips {{
        display: flex;
        flex-wrap: wrap;
        gap: 0.35rem;
        margin: 0.55rem 0 0.45rem;
      }}
      .agent-state-chip {{
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        border-radius: 999px;
        padding: 0.28rem 0.55rem;
        background: #f5ede1;
        color: var(--ink);
        font-size: 0.78rem;
        line-height: 1;
      }}
      .agent-state-chip .label {{
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
        font-size: 0.72rem;
      }}
      .agent-fields {{
        display: grid;
        gap: 0.45rem;
        margin-top: 0.55rem;
      }}
      .agent-field {{
        background: #fffaf4;
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 0.5rem 0.65rem;
      }}
      .agent-field .label {{
        display: block;
        color: var(--muted);
        font-size: 0.74rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 0.2rem;
      }}
      .agent-field .value {{
        color: var(--ink);
        line-height: 1.4;
        word-break: break-word;
      }}
      .agent-card ul {{
        margin: 0.45rem 0 0;
        padding-left: 1rem;
      }}
      .agent-card li {{
        margin: 0.2rem 0;
        color: var(--muted);
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
      .monitor-note {{
        color: var(--muted);
        font-size: 0.92rem;
      }}
    </style>
  </head>
  <body>
    <main>
      {notice_html}
      {error_html}
      <section class="hero">
        <div class="eyebrow">Harness Monitor</div>
        <h1>{escape(str(mission.get("goal") or "Current harness run"))}</h1>
        <p>This page stores raw human text in durable runtime state and exposes the current harness snapshot. It is a runtime-owned inbox and status surface, not a worker scheduler.</p>
        <div class="meta-row">
          <span>HTTP: 127.0.0.1:{escape(str(server.server_port))}</span>
          <span>Doc root: {escape(str(mission.get("doc_root") or ""))}</span>
        </div>
      </section>
      <section class="hero monitor">
        <div class="eyebrow">Live Status</div>
        <div class="monitor-grid">
          <div class="monitor-card"><strong>Mission</strong><span id="mission-status">{escape(str(mission.get("status") or "unknown"))}</span></div>
          <div class="monitor-card"><strong>Runtime</strong><span id="runtime-status">{escape(str(runtime_status))}</span></div>
          <div class="monitor-card"><strong>Round</strong><span id="runtime-round">{escape(str(state.get("current_round") or mission.get("round") or 0))}</span></div>
          <div class="monitor-card"><strong>Active Agent</strong><span id="active-agent">{escape(str(state.get("active_agent") or "idle"))}</span></div>
          <div class="monitor-card"><strong>Pending Gate</strong><span id="pending-gate">{escape(str(state.get("pending_gate_id") or gate.get("id") if gate else "" or "none"))}</span></div>
        </div>
        <p class="monitor-note" id="monitor-note">This page polls the runtime in the background. Draft replies are kept in your browser and will not disappear while you are typing.</p>
      </section>
      <section class="hero monitor">
        <div class="eyebrow">Agent Status</div>
        <div class="agent-grid" id="agent-statuses"></div>
      </section>
      <section class="hero monitor">
        <div class="eyebrow">Parallel Work</div>
        <div class="monitor-lists">
          <div class="list-card">
            <strong>Running Agents</strong>
            <ul id="running-agents-list"></ul>
          </div>
          <div class="list-card">
            <strong>Queued Work</strong>
            <ul id="queued-slices-list"></ul>
          </div>
          <div class="list-card">
            <strong>Recent Events</strong>
            <ul id="recent-events-list"></ul>
          </div>
        </div>
      </section>
      {gate_html}
      <p class="footer">The page no longer hard-refreshes while you are typing. If the gate changes mid-draft, your text stays in place and is stored as raw runtime evidence when you submit it.</p>
    </main>
    <script>
      const INITIAL_MONITOR = {json.dumps(initial_monitor, ensure_ascii=False)};
      const monitorNote = document.getElementById("monitor-note");
      const messageBox = document.getElementById("message");
      const replyForm = document.querySelector(".reply-form");
      const gateInput = document.querySelector('input[name="gate_id"]');
      const draftKey = () => {{
        const gateId = gateInput ? gateInput.value : (INITIAL_MONITOR.gate_id || "no-gate");
        return "harness-monitor-draft:" + gateId;
      }};
      const loadDraft = () => {{
        if (!messageBox) return;
        const draft = window.localStorage.getItem(draftKey());
        if (draft && !messageBox.value) {{
          messageBox.value = draft;
        }}
      }};
      const saveDraft = () => {{
        if (!messageBox) return;
        window.localStorage.setItem(draftKey(), messageBox.value);
      }};
      if (messageBox) {{
        loadDraft();
        messageBox.addEventListener("input", saveDraft);
      }}
      if (replyForm) {{
        replyForm.addEventListener("submit", () => {{
          if (messageBox && messageBox.value.trim()) {{
            window.localStorage.setItem(draftKey(), messageBox.value);
          }}
        }});
      }}
      const updateText = (id, value) => {{
        const node = document.getElementById(id);
        if (node) node.textContent = value;
      }};
      const renderList = (id, items, formatter) => {{
        const node = document.getElementById(id);
        if (!node) return;
        node.innerHTML = "";
        const list = Array.isArray(items) ? items : [];
        if (!list.length) {{
          const li = document.createElement("li");
          li.textContent = "none";
          node.appendChild(li);
          return;
        }}
        list.forEach((item) => {{
          const li = document.createElement("li");
          li.textContent = formatter(item || {{}});
          node.appendChild(li);
        }});
      }};
      const renderAgentStatuses = (items) => {{
        const node = document.getElementById("agent-statuses");
        if (!node) return;
        node.innerHTML = "";
        const list = Array.isArray(items) ? items : [];
        if (!list.length) {{
          const empty = document.createElement("div");
          empty.className = "agent-card";
          empty.innerHTML = "<div class='agent-name'>No agent status available</div>";
          node.appendChild(empty);
          return;
        }}
        list.forEach((item) => {{
          const card = document.createElement("div");
          card.className = "agent-card";
          const name = document.createElement("div");
          name.className = "agent-name";
          name.textContent = String(item.name || item.id || "Agent");
          card.appendChild(name);

          const status = document.createElement("div");
          status.className = "agent-status";
          status.textContent = String(item.status || "idle");
          card.appendChild(status);

          const summary = document.createElement("p");
          summary.textContent = String(item.summary || "");
          card.appendChild(summary);

          const chipRow = document.createElement("div");
          chipRow.className = "agent-state-chips";
          ["queued", "running", "waiting", "blocked"].forEach((field) => {{
            if (!Object.prototype.hasOwnProperty.call(item, field)) return;
            const value = item[field];
            if (value === undefined || value === null || value === "" || value === false) return;
            const chip = document.createElement("span");
            chip.className = "agent-state-chip";
            const label = document.createElement("span");
            label.className = "label";
            label.textContent = field;
            chip.appendChild(label);
            const chipValue = document.createElement("span");
            chipValue.textContent = value === true ? "active" : String(value);
            chip.appendChild(chipValue);
            chipRow.appendChild(chip);
          }});
          if (chipRow.childNodes.length) {{
            card.appendChild(chipRow);
          }}

          const fieldList = document.createElement("div");
          fieldList.className = "agent-fields";
          [
            ["worktree", "Worktree"],
            ["current_slice", "Current slice"],
            ["current_brief", "Current brief"],
          ].forEach(([field, label]) => {{
            if (!Object.prototype.hasOwnProperty.call(item, field)) return;
            const value = item[field];
            const normalized = Array.isArray(value)
              ? value.filter(Boolean).map((entry) => String(entry).trim()).join(", ")
              : String(value || "").trim();
            if (!normalized) return;
            const fieldCard = document.createElement("div");
            fieldCard.className = "agent-field";
            const fieldLabel = document.createElement("span");
            fieldLabel.className = "label";
            fieldLabel.textContent = label;
            const fieldValue = document.createElement("div");
            fieldValue.className = "value";
            fieldValue.textContent = normalized;
            fieldCard.appendChild(fieldLabel);
            fieldCard.appendChild(fieldValue);
            fieldList.appendChild(fieldCard);
          }});
          if (fieldList.childNodes.length) {{
            card.appendChild(fieldList);
          }}

          if (Array.isArray(item.details) && item.details.length) {{
            const details = document.createElement("ul");
            item.details.forEach((detail) => {{
              const li = document.createElement("li");
              li.textContent = String(detail);
              details.appendChild(li);
            }});
            card.appendChild(details);
          }}
          node.appendChild(card);
        }});
      }};
      const refreshStructuredMonitor = (monitor) => {{
        renderAgentStatuses(monitor.agent_statuses || []);
        renderList("running-agents-list", monitor.running_agents || [], (item) => {{
          const id = String(item.id || "agent");
          const status = String(item.status || "running");
          const phase = String(item.phase_title || item.slice_key || "").trim();
          return phase ? `${{id}}: ${{status}} - ${{phase}}` : `${{id}}: ${{status}}`;
        }});
        renderList("queued-slices-list", monitor.queued_slices || [], (item) => {{
          const status = String(item.status || "queued");
          const phase = String(item.phase_title || item.slice_key || "slice");
          return `${{status}} - ${{phase}}`;
        }});
        renderList("recent-events-list", monitor.recent_events || [], (item) => {{
          const when = String(item.recorded_at || "");
          const summary = String(item.summary || item.kind || "event");
          return when ? `${{when}} - ${{summary}}` : summary;
        }});
      }};
      let lastGateId = INITIAL_MONITOR.gate_id || "";
      let lastRuntimeStatus = INITIAL_MONITOR.runtime_status || "";
      refreshStructuredMonitor(INITIAL_MONITOR);
      const hasDraft = () => Boolean(messageBox && messageBox.value.trim());
      const refreshMonitor = async () => {{
        try {{
          const response = await fetch("/runtime", {{ cache: "no-store" }});
          if (!response.ok) return;
          const payload = await response.json();
          const runtime = payload.runtime || {{}};
          const mission = runtime.mission || {{}};
          const state = runtime.state || {{}};
          const communication = payload.communication || {{}};
          const openGates = Array.isArray(communication.gates) ? communication.gates.filter((gate) => gate && gate.status === "open") : [];
          const gate = openGates.length ? openGates[0] : null;
          updateText("mission-status", String(mission.status || "unknown"));
          updateText("runtime-status", String(state.status || mission.status || "unknown"));
          updateText("runtime-round", String(state.current_round || mission.round || 0));
          updateText("active-agent", String(state.active_agent || "idle"));
          updateText("pending-gate", String(state.pending_gate_id || (gate && gate.id) || "none"));
          refreshStructuredMonitor({{
            agent_statuses: runtime.agent_statuses || [],
            running_agents: runtime.running_agents || [],
            queued_slices: runtime.queued_slices || [],
            recent_events: runtime.recent_events || [],
          }});
          const nextGateId = gate ? String(gate.id || "") : "";
          const nextRuntimeStatus = String(state.status || mission.status || "unknown");
          const gateChanged = nextGateId !== lastGateId;
          const statusChanged = nextRuntimeStatus !== lastRuntimeStatus;
          if (gateChanged || statusChanged) {{
            if (hasDraft()) {{
              monitorNote.textContent = "Runtime changed while you were typing. Your draft is kept locally. Submit it, or reload when you are ready.";
            }} else {{
              window.location.reload();
              return;
            }}
          }}
          lastGateId = nextGateId;
          lastRuntimeStatus = nextRuntimeStatus;
        }} catch (_error) {{
        }}
      }};
      window.setInterval(refreshMonitor, 3000);
    </script>
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
            message = str(payload.get("message", ""))
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
            _redirect(self, "/?notice=" + quote("Reply stored in runtime state for the supervisor."))
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
