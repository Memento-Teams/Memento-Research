// src/main.js
// Initializes OMC connection, adapter, and controller.

import { OmcClient } from './omc-client.js';
import { EventAdapter } from './event-adapter.js';
import { PipelineController } from './pipeline-controller.js';

const OMC_URL = 'http://localhost:8000';

let client;
let adapter;
let controller;
// Track project IDs that this page session knows about (submitted or loaded from REST)
const _knownProjectIds = new Set();
// Buffer events for non-active projects so they don't bleed into the current view
const _eventBuffers = new Map(); // projectId → [event, ...]

async function init() {
  client = new OmcClient(OMC_URL);
  adapter = new EventAdapter();
  controller = new PipelineController(adapter);

  client.onEvent((event) => {
    // Route event to the correct project if it has a project_id
    const pid = event.payload && (event.payload.project_id || event.payload.context_id);
    if (pid) {
      const basePid = pid.split('/')[0];
      // Ignore events from projects we don't know about (stale from before page load)
      if (!_knownProjectIds.has(basePid)) return;

      // Find the tracked project ID for this event
      const trackedPid = _resolveProjectId(pid);

      // If this event belongs to a different project than the active one, buffer it
      if (window._activeProjectId && trackedPid && trackedPid !== window._activeProjectId) {
        if (!_eventBuffers.has(trackedPid)) _eventBuffers.set(trackedPid, []);
        _eventBuffers.get(trackedPid).push(event);
        // Update sidebar status dot without switching view
        if (window._getProject) {
          const proj = window._getProject(trackedPid);
          proj.status = 'processing';
          if (window._renderProjectSidebar) window._renderProjectSidebar();
        }
        return;
      }

      // First event for a new project — register and activate it
      if (!window._activeProjectId) {
        if (window._routeToProject) window._routeToProject(pid);
      }
    }
    adapter.process(event);
  });

  window._omcClient = client;
  window._controller = controller;

  try {
    await client.connect();
    _setConnectionStatus(true);
    document.getElementById('dirStatus').textContent = 'Connected — ready';

    // Fetch employee list for agent selectors
    try {
      const boot = await client.getBootstrap();
      if (boot && boot.employees) {
        window._employees = boot.employees.map(e => ({
          employee_number: e.employee_number,
          name: e.name || e.nickname,
          skills: e.skills || [],
          role: e.role || '',
        }));
        if (typeof populateAgentSelectors === 'function') populateAgentSelectors();
      }
    } catch (e) { /* bootstrap fetch is best-effort */ }

    // Load existing projects
    await loadProjects();
  } catch (err) {
    _setConnectionStatus(false);
    document.getElementById('dirStatus').textContent = 'Offline — demo mode';
    return false;
  }

  client.ws.addEventListener('close', () => _setConnectionStatus(false));
  return true;
}

async function loadProjects() {
  if (!client) return;
  try {
    const res = await client.listProjects();
    const projects = res.projects || [];
    // Register all known project IDs so their events are accepted
    for (const p of projects) {
      if (p.project_id) _knownProjectIds.add(p.project_id);
    }
    if (window.renderProjectList) {
      window.renderProjectList(projects);
    }
  } catch (e) {
    // silently fail — project list is informational
  }
}

async function launchPipeline(topic) {
  const btn = document.getElementById('launchBtn');

  if (!client || !client.ws || client.ws.readyState !== WebSocket.OPEN) {
    window.postNotice('Backend not connected. Please wait for connection.', 'error');
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Submitting...';
  document.getElementById('dirStatus').textContent = 'Submitting task...';

  try {
    const config = { projectName: `research-${Date.now()}` };
    if (typeof getStageAssignments === 'function') {
      const assignments = getStageAssignments();
      if (assignments) config.stageAssignments = assignments;
    }
    const result = await client.submitTask(topic, config);

    if (result.error) {
      window.postNotice(`Error: ${result.error}`, 'error');
      btn.textContent = 'Launch Pipeline';
      btn.disabled = false;
      document.getElementById('dirStatus').textContent = 'Error — try again';
      return;
    }

    const pid = result.project_id;
    _knownProjectIds.add(pid);
    const sessionId = result.iteration_id ? `${pid}/${result.iteration_id}` : pid;

    // Register as a new project and switch to it
    if (window._getProject) {
      const proj = window._getProject(sessionId);
      proj.task = topic;
      proj.status = 'processing';
      proj.sessionId = sessionId;
    }

    // Switch to the new project (saves previous project state, clears view)
    if (window.switchProject) {
      window.switchProject(sessionId);
    }

    // Ensure view is fresh for the new project
    document.getElementById('meetingsArea').innerHTML = '';
    document.getElementById('heroSection').style.display = 'none';

    window._currentProjectId = pid;
    window._currentSessionId = sessionId;

    // Refresh project list to include the new project
    await loadProjects();

    window.postNotice(`Research topic: <strong>${_escHtml(topic)}</strong>`, 'info');
    window.postNotice('Task accepted. Research Director delegating to team...', 'ok');
    document.getElementById('dirStatus').textContent = 'Pipeline running...';
    btn.textContent = 'Launch Pipeline';
    btn.disabled = false;
  } catch (err) {
    window.postNotice(`Submit failed: ${err.message}`, 'error');
    btn.textContent = 'Launch Pipeline';
    btn.disabled = false;
    document.getElementById('dirStatus').textContent = 'Submit failed — retry';
  }
}

function _setConnectionStatus(connected) {
  const el = document.getElementById('connStatus');
  if (!el) return;
  el.className = connected ? 'conn-status connected' : 'conn-status';
  el.querySelector('.conn-label').textContent = connected ? 'Connected' : 'Offline';
}

// Helper — _escHtml might not be defined at module scope, provide a local one
function _escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Resolve a raw project/context ID to the tracked project ID in _projects map
function _resolveProjectId(rawPid) {
  if (!rawPid) return null;
  const basePid = rawPid.split('/')[0];
  if (window._getProject && window._projects) {
    for (const [pid] of window._projects) {
      if (pid === rawPid || pid.startsWith(basePid) || rawPid.startsWith(pid.split('/')[0])) {
        return pid;
      }
    }
  }
  return null;
}

// Replay buffered events for a project when the user switches to it
function replayBufferedEvents(pid) {
  const buf = _eventBuffers.get(pid);
  if (!buf || buf.length === 0) return;
  _eventBuffers.delete(pid);
  for (const event of buf) {
    adapter.process(event);
  }
}

window.launchPipeline = launchPipeline;
window.loadProjects = loadProjects;
window.replayBufferedEvents = replayBufferedEvents;
window.resumeBreakpoint = (feedback) => controller.resumeBreakpoint(feedback);

document.addEventListener('DOMContentLoaded', () => {
  init();
});
