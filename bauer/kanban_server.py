"""kanban_server.py — Mini HTTP server para Kanban ao vivo no browser.

Serve uma página HTML de Kanban que lê TASKS.md a cada 3s via polling JSON.
Não requer bauer serve — é um servidor stdlib mínimo e independente.

Features:
  - Drag-and-drop entre colunas (HTML5 API)
  - POST /api/tasks/:id/status — mover tarefa (escreve em TASKS.md)
  - POST /api/tasks          — criar nova tarefa
  - GET  /api/info           — metadados do workspace
  - Auto-refresh a cada 3s
  - Modal de detalhes com botões de status
  - Formulário de criação de tarefa

Uso:
    from bauer.kanban_server import run_kanban_server
    run_kanban_server(workspace=Path("workspace"), port=7780)
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .workspace_manager import WorkspaceManager

# Lock global para operações de escrita em TASKS.md
_write_lock = threading.Lock()

# ── HTML + CSS + JS da página ─────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bauer Kanban</title>
<style>
  :root {
    --bg:        #0f1117;
    --surface:   #1a1d27;
    --border:    #2a2d3a;
    --text:      #e2e8f0;
    --dim:       #64748b;
    --todo:      #3b82f6;
    --ready:     #06b6d4;
    --progress:  #f59e0b;
    --blocked:   #ef4444;
    --failed:    #d946ef;
    --done:      #22c55e;
    --radius:    10px;
    --drag-over: rgba(59,130,246,0.15);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, sans-serif;
    min-height: 100vh;
    padding: 20px;
    user-select: none;
  }

  /* ── header ── */
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
  }
  header h1 { font-size: 1.4rem; font-weight: 700; letter-spacing: -0.5px; }
  header h1 span { color: var(--todo); }
  #status {
    font-size: 0.75rem;
    color: var(--dim);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  #dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--done);
    animation: pulse 2s infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

  /* ── board ── */
  .board {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 16px;
    align-items: start;
  }
  @media (max-width: 900px) { .board { grid-template-columns: repeat(2,1fr); } }
  @media (max-width: 500px)  { .board { grid-template-columns: 1fr; } }

  /* ── column ── */
  .column {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    transition: border-color 0.15s;
  }
  .column.drag-over {
    border-color: var(--todo);
    background: color-mix(in srgb, var(--surface) 90%, var(--todo) 10%);
  }
  .col-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    font-size: 0.8rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    border-bottom: 2px solid var(--border);
  }
  .col-header.todo     { border-color: var(--todo); }
  .col-header.ready    { border-color: var(--ready); }
  .col-header.progress { border-color: var(--progress); }
  .col-header.blocked  { border-color: var(--blocked); }
  .col-header.failed   { border-color: var(--failed); }
  .col-header.done     { border-color: var(--done); }
  .col-header .label.todo     { color: var(--todo); }
  .col-header .label.ready    { color: var(--ready); }
  .col-header .label.progress { color: var(--progress); }
  .col-header .label.blocked  { color: var(--blocked); }
  .col-header .label.failed   { color: var(--failed); }
  .col-header .label.done     { color: var(--done); }
  .col-header-right { display:flex; align-items:center; gap:8px; }
  .badge {
    background: var(--border);
    border-radius: 12px;
    padding: 2px 8px;
    font-size: 0.7rem;
    color: var(--dim);
  }
  .btn-add {
    background: none;
    border: 1px solid var(--border);
    color: var(--dim);
    border-radius: 6px;
    padding: 2px 6px;
    font-size: 0.7rem;
    cursor: pointer;
    transition: color 0.12s, border-color 0.12s;
    line-height: 1.4;
  }
  .btn-add:hover { color: var(--text); border-color: var(--dim); }

  /* ── cards ── */
  .cards {
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-height: 60px;
  }
  .card {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
    cursor: grab;
    transition: border-color 0.15s, transform 0.1s, box-shadow 0.15s, opacity 0.15s;
  }
  .card:hover {
    border-color: var(--todo);
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  }
  .card.dragging {
    opacity: 0.35;
    cursor: grabbing;
    transform: scale(0.97);
  }
  .card-id {
    font-size: 0.65rem;
    color: var(--dim);
    font-family: monospace;
    margin-bottom: 4px;
  }
  .card-title {
    font-size: 0.85rem;
    font-weight: 600;
    line-height: 1.4;
    color: var(--text);
    cursor: pointer;
  }
  .card-desc {
    font-size: 0.75rem;
    color: var(--dim);
    margin-top: 6px;
    line-height: 1.4;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .spec-badge {
    display: inline-block;
    margin-top: 6px;
    font-size: 0.65rem;
    background: rgba(59,130,246,0.15);
    color: var(--todo);
    border-radius: 4px;
    padding: 1px 5px;
  }
  .compact .card-desc { display: none; }
  .show-more {
    text-align: center;
    font-size: 0.72rem;
    color: var(--dim);
    padding: 8px 0 4px;
    cursor: pointer;
    user-select: none;
  }
  .show-more:hover { color: var(--text); }
  .empty {
    text-align: center;
    color: var(--dim);
    font-size: 0.8rem;
    padding: 24px 0;
  }
  .drop-hint {
    border: 2px dashed var(--border);
    border-radius: 8px;
    height: 60px;
    display: none;
    align-items: center;
    justify-content: center;
    font-size: 0.75rem;
    color: var(--dim);
  }
  .column.drag-over .drop-hint { display: flex; }

  /* ── modal de detalhes ── */
  .overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.65);
    backdrop-filter: blur(3px);
    z-index: 100;
    align-items: center;
    justify-content: center;
  }
  .overlay.open { display: flex; }
  .modal {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 28px 32px;
    max-width: 540px;
    width: 90%;
    position: relative;
    animation: slideIn 0.18s ease;
  }
  @keyframes slideIn { from{opacity:0;transform:translateY(16px)} to{opacity:1;transform:none} }
  .modal-close {
    position: absolute;
    top: 14px; right: 18px;
    background: none;
    border: none;
    color: var(--dim);
    font-size: 1.3rem;
    cursor: pointer;
    line-height: 1;
  }
  .modal-close:hover { color: var(--text); }

  /* Detail modal */
  #modal-id { font-size: 0.7rem; font-family: monospace; color: var(--dim); margin-bottom: 8px; }
  #modal-status {
    display: inline-block;
    font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.6px;
    border-radius: 6px; padding: 2px 8px; margin-bottom: 14px;
  }
  #modal-status.TODO       { background:rgba(59,130,246,.15); color:var(--todo); }
  #modal-status.READY      { background:rgba(6,182,212,.15);  color:var(--ready); }
  #modal-status.IN_PROGRESS{ background:rgba(245,158,11,.15); color:var(--progress); }
  #modal-status.BLOCKED    { background:rgba(239,68,68,.15);  color:var(--blocked); }
  #modal-status.FAILED     { background:rgba(217,70,239,.15); color:var(--failed); }
  #modal-status.DONE       { background:rgba(34,197,94,.15);  color:var(--done); }
  #modal-title { font-size: 1.05rem; font-weight: 700; line-height: 1.5; margin-bottom: 16px; }
  #modal-divider { border: none; border-top: 1px solid var(--border); margin-bottom: 14px; }
  #modal-desc { font-size: 0.85rem; color: var(--dim); line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
  #modal-spec { margin-top: 16px; font-size: 0.75rem; background: rgba(59,130,246,0.1); color: var(--todo); border-radius: 6px; padding: 6px 10px; display: none; }

  /* Status buttons */
  .status-btns { margin-top: 20px; display: flex; gap: 8px; flex-wrap: wrap; }
  .status-btn {
    border: none; border-radius: 7px; padding: 6px 14px;
    font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
    cursor: pointer; transition: opacity 0.15s;
  }
  .status-btn:hover { opacity: 0.8; }
  .status-btn.TODO       { background:rgba(59,130,246,.2);  color:var(--todo); }
  .status-btn.READY      { background:rgba(6,182,212,.2);   color:var(--ready); }
  .status-btn.IN_PROGRESS{ background:rgba(245,158,11,.2);  color:var(--progress); }
  .status-btn.BLOCKED    { background:rgba(239,68,68,.2);   color:var(--blocked); }
  .status-btn.FAILED     { background:rgba(217,70,239,.2);  color:var(--failed); }
  .status-btn.DONE       { background:rgba(34,197,94,.2);   color:var(--done); }
  .status-btn.active     { outline: 2px solid currentColor; }
  #modal-hint { margin-top: 14px; font-size: 0.7rem; color: var(--dim); text-align: right; }

  /* New task modal */
  #new-task-overlay .modal { max-width: 460px; }
  .form-label { font-size: 0.78rem; font-weight: 600; color: var(--dim); margin-bottom: 6px; display:block; }
  .form-input, .form-textarea {
    width: 100%; background: var(--bg); border: 1px solid var(--border);
    border-radius: 8px; color: var(--text); font-size: 0.85rem;
    padding: 8px 12px; outline: none; transition: border-color 0.12s;
    font-family: inherit; resize: vertical;
  }
  .form-input:focus, .form-textarea:focus { border-color: var(--todo); }
  .form-textarea { min-height: 80px; }
  .form-group { margin-bottom: 16px; }
  .form-row { display:flex; gap:12px; }
  .form-row .form-group { flex:1; }
  .form-select {
    width: 100%; background: var(--bg); border: 1px solid var(--border);
    border-radius: 8px; color: var(--text); font-size: 0.85rem;
    padding: 8px 12px; outline: none; cursor: pointer;
  }
  .form-select:focus { border-color: var(--todo); }
  .btn-primary {
    background: var(--todo); color: #fff; border: none;
    border-radius: 8px; padding: 9px 20px; font-size: 0.85rem;
    font-weight: 700; cursor: pointer; transition: opacity 0.15s;
  }
  .btn-primary:hover { opacity: 0.85; }
  .btn-secondary {
    background: transparent; color: var(--dim); border: 1px solid var(--border);
    border-radius: 8px; padding: 9px 20px; font-size: 0.85rem;
    cursor: pointer; transition: color 0.12s;
  }
  .btn-secondary:hover { color: var(--text); }
  .form-actions { display:flex; justify-content:flex-end; gap:10px; margin-top: 20px; }
  .modal-title { font-size: 1rem; font-weight: 700; margin-bottom: 20px; }
  .toast {
    position: fixed; bottom: 24px; right: 24px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 10px; padding: 12px 18px;
    font-size: 0.82rem; color: var(--text);
    box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    animation: toastIn 0.2s ease; z-index: 200;
  }
  .toast.success { border-color: var(--done); }
  .toast.error   { border-color: var(--blocked); }
  @keyframes toastIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:none} }
</style>
</head>
<body>

<!-- header -->
<header>
  <div style="display:flex;align-items:center;gap:12px">
    <h1><span>Bauer</span> Kanban</h1>
    <span id="company-tag" style="display:none;font-size:0.75rem;background:rgba(59,130,246,0.15);color:var(--todo);border-radius:6px;padding:3px 10px;font-weight:600">
      <span id="company-name"></span>
    </span>
  </div>
  <div id="status">
    <div id="dot"></div>
    <span id="ts">atualizando...</span>
  </div>
</header>

<!-- board -->
<div class="board" id="board">
  <div class="column" id="col-TODO" ondragover="onDragOver(event,'TODO')" ondrop="onDrop(event,'TODO')" ondragleave="onDragLeave(event)">
    <div class="col-header todo">
      <span class="label todo">TODO</span>
      <div class="col-header-right">
        <span class="badge" id="cnt-TODO">0</span>
        <button class="btn-add" onclick="openNewTask('TODO')" title="Nova tarefa">+ novo</button>
      </div>
    </div>
    <div class="cards" id="cards-TODO"><div class="drop-hint">Soltar aqui</div></div>
  </div>
  <div class="column" id="col-READY" ondragover="onDragOver(event,'READY')" ondrop="onDrop(event,'READY')" ondragleave="onDragLeave(event)">
    <div class="col-header ready">
      <span class="label ready">READY</span>
      <div class="col-header-right">
        <span class="badge" id="cnt-READY">0</span>
        <button class="btn-add" onclick="openNewTask('READY')" title="Nova tarefa">+ novo</button>
      </div>
    </div>
    <div class="cards" id="cards-READY"><div class="drop-hint">Soltar aqui</div></div>
  </div>
  <div class="column" id="col-IN_PROGRESS" ondragover="onDragOver(event,'IN_PROGRESS')" ondrop="onDrop(event,'IN_PROGRESS')" ondragleave="onDragLeave(event)">
    <div class="col-header progress">
      <span class="label progress">EM PROGRESSO</span>
      <div class="col-header-right">
        <span class="badge" id="cnt-IN_PROGRESS">0</span>
        <button class="btn-add" onclick="openNewTask('IN_PROGRESS')" title="Nova tarefa">+ novo</button>
      </div>
    </div>
    <div class="cards" id="cards-IN_PROGRESS"><div class="drop-hint">Soltar aqui</div></div>
  </div>
  <div class="column" id="col-BLOCKED" ondragover="onDragOver(event,'BLOCKED')" ondrop="onDrop(event,'BLOCKED')" ondragleave="onDragLeave(event)">
    <div class="col-header blocked">
      <span class="label blocked">BLOQUEADO</span>
      <div class="col-header-right">
        <span class="badge" id="cnt-BLOCKED">0</span>
        <button class="btn-add" onclick="openNewTask('BLOCKED')" title="Nova tarefa">+ novo</button>
      </div>
    </div>
    <div class="cards" id="cards-BLOCKED"><div class="drop-hint">Soltar aqui</div></div>
  </div>
  <div class="column" id="col-FAILED" ondragover="onDragOver(event,'FAILED')" ondrop="onDrop(event,'FAILED')" ondragleave="onDragLeave(event)">
    <div class="col-header failed">
      <span class="label failed">FALHOU</span>
      <div class="col-header-right">
        <span class="badge" id="cnt-FAILED">0</span>
        <button class="btn-add" onclick="openNewTask('FAILED')" title="Nova tarefa">+ novo</button>
      </div>
    </div>
    <div class="cards" id="cards-FAILED"><div class="drop-hint">Soltar aqui</div></div>
  </div>
  <div class="column" id="col-DONE" ondragover="onDragOver(event,'DONE')" ondrop="onDrop(event,'DONE')" ondragleave="onDragLeave(event)">
    <div class="col-header done">
      <span class="label done">CONCLUIDO</span>
      <div class="col-header-right">
        <span class="badge" id="cnt-DONE">0</span>
        <button class="btn-add" onclick="openNewTask('DONE')" title="Nova tarefa">+ novo</button>
      </div>
    </div>
    <div class="cards" id="cards-DONE"><div class="drop-hint">Soltar aqui</div></div>
  </div>
</div>

<!-- Modal detalhes -->
<div class="overlay" id="modal-overlay" onclick="closeDetail(event)">
  <div class="modal" id="modal">
    <button class="modal-close" onclick="closeDetail()">&times;</button>
    <div id="modal-id"></div>
    <div id="modal-status"></div>
    <div id="modal-title"></div>
    <hr id="modal-divider">
    <div id="modal-desc"></div>
    <div id="modal-spec"></div>
    <div class="status-btns" id="modal-status-btns"></div>
    <div id="modal-hint">Arraste o card para mover • Esc para fechar</div>
  </div>
</div>

<!-- Modal nova tarefa -->
<div class="overlay" id="new-task-overlay" onclick="closeNewTask(event)">
  <div class="modal">
    <button class="modal-close" onclick="closeNewTask()">&times;</button>
    <div class="modal-title">Nova Tarefa</div>
    <div class="form-group">
      <label class="form-label">Título *</label>
      <input class="form-input" id="nt-title" placeholder="Título da tarefa" maxlength="200">
    </div>
    <div class="form-group">
      <label class="form-label">Descrição</label>
      <textarea class="form-textarea" id="nt-desc" placeholder="Descrição opcional..."></textarea>
    </div>
    <div class="form-row">
      <div class="form-group">
        <label class="form-label">Status inicial</label>
        <select class="form-select" id="nt-status">
          <option value="TODO">TODO</option>
          <option value="READY">READY</option>
          <option value="IN_PROGRESS">EM PROGRESSO</option>
          <option value="BLOCKED">BLOQUEADO</option>
          <option value="FAILED">FALHOU</option>
          <option value="DONE">CONCLUÍDO</option>
        </select>
      </div>
      <div class="form-group">
        <label class="form-label">Spec vinculado</label>
        <input class="form-input" id="nt-spec" placeholder="ex: auth-login (opcional)">
      </div>
    </div>
    <div class="form-actions">
      <button class="btn-secondary" onclick="closeNewTask()">Cancelar</button>
      <button class="btn-primary" onclick="submitNewTask()">Criar Tarefa</button>
    </div>
  </div>
</div>

<script>
const COMPACT_THRESHOLD = 8;
const REFRESH_MS = 3000;
const collapseState = {};
let allTasks = [];
let dragTaskId = null;
let currentDetailId = null;
let refreshPaused = false;

const STATUSES = ["TODO","READY","IN_PROGRESS","BLOCKED","FAILED","DONE"];
const STATUS_LABELS = {TODO:"TODO",READY:"READY",IN_PROGRESS:"EM PROGRESSO",BLOCKED:"BLOQUEADO",FAILED:"FALHOU",DONE:"CONCLUIDO"};

function esc(s) {
  return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// ── Toast ──────────────────────────────────────────────────────────────────
function toast(msg, type="success") {
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// ── Status update ──────────────────────────────────────────────────────────
async function moveTask(taskId, newStatus) {
  try {
    const r = await fetch(`/api/tasks/${taskId}/status`, {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({status: newStatus})
    });
    const d = await r.json();
    if (!r.ok) { toast("Erro: " + (d.error||"falha"), "error"); return false; }
    // Atualiza local imediatamente (sem esperar polling)
    const t = allTasks.find(x => x.id === taskId);
    if (t) { t.status = newStatus; render(); }
    toast("Movido para " + STATUS_LABELS[newStatus]);
    return true;
  } catch(e) {
    toast("Erro de conexão", "error");
    return false;
  }
}

// ── Create task ────────────────────────────────────────────────────────────
async function submitNewTask() {
  const title = document.getElementById("nt-title").value.trim();
  if (!title) { document.getElementById("nt-title").focus(); return; }
  const status = document.getElementById("nt-status").value;
  const desc   = document.getElementById("nt-desc").value.trim();
  const spec   = document.getElementById("nt-spec").value.trim();

  try {
    const r = await fetch("/api/tasks", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({title, status, description: desc, spec_id: spec})
    });
    const d = await r.json();
    if (!r.ok) { toast("Erro: " + (d.error||"falha"), "error"); return; }
    toast("Tarefa criada: #" + d.id);
    closeNewTask();
    await fetchAndRender();
  } catch(e) {
    toast("Erro de conexão", "error");
  }
}

// ── New task modal ─────────────────────────────────────────────────────────
function openNewTask(status) {
  document.getElementById("nt-title").value = "";
  document.getElementById("nt-desc").value = "";
  document.getElementById("nt-spec").value = "";
  document.getElementById("nt-status").value = status || "TODO";
  document.getElementById("new-task-overlay").classList.add("open");
  document.getElementById("nt-title").focus();
}
function closeNewTask(e) {
  if (e && e.target !== document.getElementById("new-task-overlay")) return;
  document.getElementById("new-task-overlay").classList.remove("open");
}

// ── Detail modal ───────────────────────────────────────────────────────────
function openDetail(taskId) {
  const t = allTasks.find(x => x.id === taskId);
  if (!t) return;
  currentDetailId = taskId;
  refreshPaused = true;

  document.getElementById("modal-id").textContent = "#" + t.id;
  const statusEl = document.getElementById("modal-status");
  statusEl.textContent = STATUS_LABELS[t.status] || t.status;
  statusEl.className = t.status;
  document.getElementById("modal-title").textContent = t.title;
  const descEl = document.getElementById("modal-desc");
  descEl.textContent = t.description || "Sem descrição.";
  descEl.style.color = t.description ? "var(--dim)" : "var(--dim)";
  document.getElementById("modal-divider").style.display = "block";

  const specEl = document.getElementById("modal-spec");
  if (t.spec_id) { specEl.textContent = "Spec vinculado: " + t.spec_id; specEl.style.display = "block"; }
  else { specEl.style.display = "none"; }

  // Botões de status
  const btns = document.getElementById("modal-status-btns");
  btns.innerHTML = STATUSES.map(s =>
    `<button class="status-btn ${s}${s===t.status?' active':''}" onclick="changeStatus('${taskId}','${s}')">${STATUS_LABELS[s]}</button>`
  ).join("");

  document.getElementById("modal-overlay").classList.add("open");
}

async function changeStatus(taskId, newStatus) {
  const ok = await moveTask(taskId, newStatus);
  if (ok) {
    const t = allTasks.find(x => x.id === taskId);
    if (t) { t.status = newStatus; }
    // Atualiza modal
    const statusEl = document.getElementById("modal-status");
    statusEl.textContent = STATUS_LABELS[newStatus];
    statusEl.className = newStatus;
    const btns = document.getElementById("modal-status-btns");
    btns.querySelectorAll(".status-btn").forEach(b => {
      b.classList.toggle("active", b.classList.contains(newStatus));
    });
  }
}

function closeDetail(e) {
  if (e && e.target !== document.getElementById("modal-overlay") && e.target !== document.getElementById("modal-close")) return;
  document.getElementById("modal-overlay").classList.remove("open");
  currentDetailId = null;
  refreshPaused = false;
}

// ── Drag and Drop ──────────────────────────────────────────────────────────
function onDragStart(e, taskId) {
  dragTaskId = taskId;
  e.dataTransfer.effectAllowed = "move";
  setTimeout(() => {
    const el = document.querySelector(`.card[data-id="${taskId}"]`);
    if (el) el.classList.add("dragging");
  }, 0);
}
function onDragEnd(e) {
  dragTaskId = null;
  document.querySelectorAll(".card.dragging").forEach(el => el.classList.remove("dragging"));
  document.querySelectorAll(".column.drag-over").forEach(el => el.classList.remove("drag-over"));
}
function onDragOver(e, status) {
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  const col = document.getElementById("col-" + status);
  if (col) col.classList.add("drag-over");
}
function onDragLeave(e) {
  const col = e.currentTarget;
  if (col) col.classList.remove("drag-over");
}
async function onDrop(e, newStatus) {
  e.preventDefault();
  const col = document.getElementById("col-" + newStatus);
  if (col) col.classList.remove("drag-over");
  if (!dragTaskId) return;
  const t = allTasks.find(x => x.id === dragTaskId);
  if (t && t.status !== newStatus) {
    await moveTask(dragTaskId, newStatus);
  }
  dragTaskId = null;
}

// ── Cards ──────────────────────────────────────────────────────────────────
function renderCard(t, compact) {
  const desc = (!compact && t.description)
    ? `<div class="card-desc">${esc(t.description)}</div>` : "";
  const spec = (!compact && t.spec_id)
    ? `<span class="spec-badge">spec: ${esc(t.spec_id)}</span>` : "";
  return `
    <div class="card${compact?" compact":""}" data-id="${esc(t.id)}"
         draggable="true"
         ondragstart="onDragStart(event,'${esc(t.id)}')"
         ondragend="onDragEnd(event)">
      <div class="card-id">#${esc(t.id)}</div>
      <div class="card-title" onclick="openDetail('${esc(t.id)}')">${esc(t.title)}</div>
      ${desc}${spec}
    </div>`;
}

function renderColumn(status, tasks) {
  const el  = document.getElementById("cards-" + status);
  const cnt = document.getElementById("cnt-"   + status);
  if (!el) return;
  cnt.textContent = tasks.length;

  const dropHint = '<div class="drop-hint">Soltar aqui</div>';

  if (!tasks.length) {
    el.innerHTML = dropHint + '<div class="empty">vazio</div>';
    return;
  }

  const compact   = tasks.length > COMPACT_THRESHOLD;
  const collapsed = collapseState[status] !== false && compact;
  const visible   = collapsed ? tasks.slice(-COMPACT_THRESHOLD) : tasks;
  const hidden    = tasks.length - visible.length;

  let html = dropHint;
  if (hidden > 0)
    html += `<div class="show-more" onclick="expand('${status}')">&#9650; ${hidden} mais antigos &mdash; clique para expandir</div>`;
  html += visible.map(t => renderCard(t, compact && collapsed)).join("");
  el.innerHTML = html;
}

function expand(status) { collapseState[status] = false; render(); }

function render() {
  const grouped = { TODO:[], READY:[], IN_PROGRESS:[], BLOCKED:[], FAILED:[], DONE:[] };
  for (const t of allTasks)
    if (grouped[t.status] !== undefined) grouped[t.status].push(t);
  for (const [s, tasks] of Object.entries(grouped)) renderColumn(s, tasks);
}

// ── Polling ────────────────────────────────────────────────────────────────
async function fetchAndRender() {
  if (refreshPaused) return;
  try {
    const r    = await fetch("/api/tasks");
    const data = await r.json();
    allTasks   = data.tasks || [];
    render();
    document.getElementById("ts").textContent =
      "ao vivo \xb7 " + new Date().toLocaleTimeString("pt-BR");
    document.getElementById("dot").style.background = "var(--done)";
  } catch(e) {
    document.getElementById("ts").textContent =
      "erro de conexao \xb7 " + new Date().toLocaleTimeString("pt-BR");
    document.getElementById("dot").style.background = "var(--blocked)";
  }
}

// ── Info ───────────────────────────────────────────────────────────────────
async function fetchInfo() {
  try {
    const r = await fetch("/api/info");
    const d = await r.json();
    if (d.company_name) {
      document.getElementById("company-name").textContent = d.company_name;
      document.getElementById("company-tag").style.display = "inline-block";
      document.title = "Kanban — " + d.company_name;
    }
  } catch(e) {}
}

// ── Teclado ────────────────────────────────────────────────────────────────
document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    closeDetail();
    closeNewTask();
  }
  // N para nova tarefa (quando nenhum modal está aberto)
  if (e.key === "n" && !document.querySelector(".overlay.open")) {
    openNewTask("TODO");
  }
});

fetchInfo();
fetchAndRender();
setInterval(fetchAndRender, REFRESH_MS);
</script>
</body>
</html>"""


# ── Handler HTTP ───────────────────────────────────────────────────────────────


class _KanbanHandler(BaseHTTPRequestHandler):
    """Handler minimalista — serve HTML e JSON, suporta GET e POST."""

    workspace: Path     # injetado pela factory
    company_name: str   # injetado pela factory

    def log_message(self, format, *args):  # silencia logs do stdlib
        pass

    # ── GET ────────────────────────────────────────────────────────────────

    def do_GET(self):
        if self.path in ("/", "/kanban"):
            self._serve_html()
        elif self.path == "/api/tasks":
            self._serve_tasks()
        elif self.path == "/api/info":
            self._serve_info()
        else:
            self._send(404, "text/plain", b"Not found")

    # ── POST ───────────────────────────────────────────────────────────────

    def do_POST(self):
        """POST /api/tasks/:id/status  → move tarefa
           POST /api/tasks             → cria tarefa
        """
        path = self.path.rstrip("/")

        # POST /api/tasks/{id}/status
        import re as _re
        _m = _re.match(r"^/api/tasks/(\d+)/status$", path)
        if _m:
            task_id = _m.group(1).zfill(3)
            body = self._read_body()
            if body is None:
                return
            new_status = body.get("status", "").strip().upper()
            valid = {"TODO", "READY", "IN_PROGRESS", "DONE", "BLOCKED", "FAILED"}
            if new_status not in valid:
                self._json(400, {"error": f"Status inválido: '{new_status}'. Válidos: {sorted(valid)}"})
                return
            try:
                with _write_lock:
                    wm = WorkspaceManager(self.workspace)
                    if new_status == "READY":
                        from .task_dispatcher import TaskDispatcher
                        task = TaskDispatcher(self.workspace).mark_ready(task_id)
                    else:
                        task = wm.update_task_status(task_id, new_status)
                        wm.update_task_metadata(
                            task.id,
                            metadata={
                                "claim_id": None,
                                "claim_expires": None,
                                "claimed_by": None,
                                "worker_pid": None,
                                "heartbeat_at": None,
                            },
                        )
                        task = wm.get_task(task.id)
                self._json(200, {
                    "id": task.id, "status": task.status,
                    "title": task.title,
                })
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return

        # POST /api/tasks (criar nova tarefa)
        if path == "/api/tasks":
            body = self._read_body()
            if body is None:
                return
            title = (body.get("title") or "").strip()
            if not title:
                self._json(400, {"error": "Campo 'title' é obrigatório."})
                return
            description = (body.get("description") or "").strip()
            spec_id = (body.get("spec_id") or "").strip()
            priority = (body.get("priority") or "medium").strip().lower()
            assignee = (body.get("assignee") or "").strip()
            parent_id = (body.get("parent_id") or "").strip()
            status = (body.get("status") or "TODO").strip().upper()
            valid = {"TODO", "READY", "IN_PROGRESS", "DONE", "BLOCKED", "FAILED"}
            if status not in valid:
                status = "TODO"
            try:
                with _write_lock:
                    wm = WorkspaceManager(self.workspace)
                    if not wm.tasks_file.exists():
                        wm.init_project("Projeto")
                    metadata = {"dispatch": "true"} if status == "READY" else None
                    task = wm.add_task(
                        title,
                        description=description,
                        spec_id=spec_id,
                        status=status,
                        priority=priority,
                        assignee=assignee,
                        parent_id=parent_id,
                        metadata=metadata,
                    )
                self._json(201, {
                    "id": task.id, "status": task.status,
                    "title": task.title,
                })
            except Exception as exc:
                self._json(500, {"error": str(exc)})
            return

        self._send(404, "text/plain", b"Not found")

    # ── helpers ────────────────────────────────────────────────────────────

    def _read_body(self) -> dict | None:
        """Lê e parseia body JSON. Retorna None e envia 400 em caso de erro."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._json(400, {"error": f"JSON inválido: {exc}"})
            return None

    def _serve_html(self):
        self._send(200, "text/html; charset=utf-8", _HTML.encode())

    def _serve_info(self):
        payload = {
            "company_name": getattr(self.__class__, "company_name", ""),
            "workspace": str(getattr(self.__class__, "workspace", "")),
        }
        body = json.dumps(payload, ensure_ascii=False).encode()
        self._send(200, "application/json; charset=utf-8", body)

    def _serve_tasks(self):
        try:
            wm = WorkspaceManager(self.workspace)
            tasks = wm.list_tasks()
            payload = {
                "tasks": [
                    {
                        "id": t.id,
                        "status": t.status,
                        "title": t.title,
                        "description": t.description[:200] if t.description else "",
                        "spec_id": t.spec_id,
                        "priority": t.priority,
                        "assignee": t.assignee,
                        "parent_id": t.parent_id,
                        "comments": t.comments,
                    }
                    for t in tasks
                ]
            }
            body = json.dumps(payload, ensure_ascii=False).encode()
            self._send(200, "application/json; charset=utf-8", body)
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self._send(500, "application/json", body)

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self._send(code, "application/json; charset=utf-8", body)

    def _send(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ── API pública ───────────────────────────────────────────────────────────────


def run_kanban_server(
    workspace: Path | str = "workspace",
    host: str = "127.0.0.1",
    port: int = 7780,
    open_browser: bool = True,
    company_name: str = "",
) -> None:
    """Sobe o servidor Kanban e bloqueia até Ctrl+C.

    Args:
        workspace:     Caminho do workspace com TASKS.md.
        host:          Interface de escuta.
        port:          Porta HTTP.
        open_browser:  Se True, abre o browser automaticamente.
        company_name:  Nome da empresa (exibido no título da página).
    """
    workspace = Path(workspace).resolve()

    # Injeta workspace e company_name no handler via class attributes
    server = HTTPServer((host, port), _KanbanHandler)
    server.RequestHandlerClass.workspace = workspace        # type: ignore[attr-defined]
    server.RequestHandlerClass.company_name = company_name  # type: ignore[attr-defined]

    url = f"http://{host}:{port}"

    if open_browser:
        # Abre o browser em thread separada (dá tempo do server subir)
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def start_kanban_thread(
    workspace: Path | str = "workspace",
    host: str = "127.0.0.1",
    port: int = 7780,
    open_browser: bool = True,
    company_name: str = "",
) -> threading.Thread:
    """Sobe o servidor Kanban em daemon thread. Retorna o thread."""

    def _run():
        run_kanban_server(
            workspace=workspace, host=host, port=port,
            open_browser=open_browser, company_name=company_name,
        )

    t = threading.Thread(target=_run, daemon=True, name="bauer-kanban")
    t.start()
    return t
