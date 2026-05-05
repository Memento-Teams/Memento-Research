// src/main.js
// Initializes OMC connection, adapter, and controller.

import { OmcClient } from './omc-client.js';
import { EventAdapter } from './event-adapter.js';
import { PipelineController } from './pipeline-controller.js';

const OMC_URL = 'http://localhost:8000';

let client;
let adapter;
let controller;

async function init() {
  client = new OmcClient(OMC_URL);
  adapter = new EventAdapter();
  controller = new PipelineController(adapter);

  // Wire: OMC events → adapter → controller
  client.onEvent((event) => adapter.process(event));

  // Expose for other modules (breakpoint resume etc.)
  window._omcClient = client;
  window._controller = controller;

  try {
    await client.connect();
    setConnectionStatus(true);
    addEvent('stag', 'Connected to OMC backend.');
    document.getElementById('dirStatus').textContent = 'Connected — ready';
  } catch (err) {
    setConnectionStatus(false);
    addEvent('stag', `Connection failed: ${err.message || 'unreachable'}. Running in demo mode.`);
    document.getElementById('dirStatus').textContent = 'Offline — demo mode';
    return false;
  }

  // Track connection state changes
  client.ws.addEventListener('close', () => setConnectionStatus(false));

  return true;
}

async function launchPipeline(topic) {
  if (!client || !client.ws || client.ws.readyState !== WebSocket.OPEN) {
    // Fallback to demo if not connected
    startDemo();
    return;
  }

  addEvent('dtag', `Submitting: "${topic}"`);
  document.getElementById('dirStatus').textContent = 'Submitting task...';

  const result = await client.submitTask(topic, {
    projectName: `research-${Date.now()}`,
  });

  if (result.error) {
    addEvent('stag', `Error: ${result.error}`);
    return;
  }

  window._currentProjectId = result.project_id;
  addEvent('dtag', `Task accepted. Project: ${result.project_id}`);
  document.getElementById('dirStatus').textContent = 'Pipeline running...';
}

function setConnectionStatus(connected) {
  const el = document.getElementById('connStatus');
  if (!el) return;
  el.className = connected ? 'conn-status connected' : 'conn-status';
  el.querySelector('.conn-label').textContent = connected ? 'Connected' : 'Offline';
}

// Expose for HTML
window.launchPipeline = launchPipeline;
window.resumeBreakpoint = (feedback) => controller.resumeBreakpoint(feedback);

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
  init();
});
