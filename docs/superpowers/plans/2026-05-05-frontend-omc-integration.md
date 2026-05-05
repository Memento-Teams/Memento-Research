# Frontend ↔ OMC Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the AutoResearch frontend prototype to a live OMC backend, replacing the hardcoded `startDemo()` simulation with real WebSocket events and REST API calls.

**Architecture:** The frontend connects to OMC via WebSocket for real-time events (meeting progress, stage transitions, gate decisions) and REST for commands (submit task, resume breakpoint, override critic). A thin adapter layer translates OMC's generic CompanyEvent format into AutoResearch's domain-specific meeting card model.

**Tech Stack:** Vanilla JS (existing frontend), OMC FastAPI backend (WebSocket + REST), no build tools.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `index.html` | Existing UI — will be modified to remove hardcoded demo, wire up real connection |
| `src/omc-client.js` | WebSocket connection + REST API wrapper. Single point of contact with OMC. |
| `src/event-adapter.js` | Translates OMC CompanyEvents into AutoResearch domain events (meeting cards, stage state, gate decisions) |
| `src/pipeline-controller.js` | Orchestrates UI updates: creates meeting cards, drives stage state machine, manages breakpoints |
| `src/main.js` | Entry point: initializes client, adapter, controller; handles topic submission |

---

## Task 1: OMC Client Module

**Files:**
- Create: `src/omc-client.js`

- [ ] **Step 1: Create the WebSocket + REST client**

```javascript
// src/omc-client.js
// Connects to OMC backend, exposes event stream + command methods.

export class OmcClient {
  constructor(baseUrl = 'http://localhost:8000') {
    this.baseUrl = baseUrl;
    this.ws = null;
    this.listeners = [];
  }

  // --- WebSocket ---

  connect() {
    const wsUrl = this.baseUrl.replace(/^http/, 'ws') + '/ws';
    this.ws = new WebSocket(wsUrl);

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.listeners.forEach(fn => fn(data));
    };

    this.ws.onclose = () => {
      // Reconnect after 3s
      setTimeout(() => this.connect(), 3000);
    };

    return new Promise((resolve, reject) => {
      this.ws.onopen = () => resolve();
      this.ws.onerror = (err) => reject(err);
    });
  }

  onEvent(fn) {
    this.listeners.push(fn);
    return () => { this.listeners = this.listeners.filter(l => l !== fn); };
  }

  // --- REST Commands ---

  async submitTask(topic, config = {}) {
    const form = new FormData();
    form.append('task', topic);
    if (config.projectName) form.append('project_name', config.projectName);
    form.append('mode', 'standard');

    const res = await fetch(`${this.baseUrl}/api/ceo/task`, { method: 'POST', body: form });
    return res.json();
  }

  async getBootstrap() {
    const res = await fetch(`${this.baseUrl}/api/bootstrap`);
    return res.json();
  }

  async getProjectTree(projectId) {
    const res = await fetch(`${this.baseUrl}/api/projects/${projectId}/tree`);
    return res.json();
  }

  async sendMeetingChat(roomId, message) {
    const res = await fetch(`${this.baseUrl}/api/meeting/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_id: roomId, message }),
    });
    return res.json();
  }
}
```

- [ ] **Step 2: Verify OMC is running and WebSocket connects**

Start OMC in a separate terminal:
```bash
cd /Users/yuzhengxu/projects/OneManCompany/OneManCompany && python -m onemancompany.main
```

Then open browser console and test:
```javascript
import { OmcClient } from './src/omc-client.js';
const client = new OmcClient();
await client.connect(); // Should resolve without error
client.onEvent(e => console.log('EVENT:', e));
```

Expected: `EVENT: { type: "connected", payload: { message: "Bootstrap from REST API" } }`

- [ ] **Step 3: Commit**

```bash
git add src/omc-client.js
git commit -m "feat: add OMC WebSocket + REST client module"
```

---

## Task 2: Event Adapter

**Files:**
- Create: `src/event-adapter.js`

The adapter translates OMC's generic event format `{ type, agent, payload }` into AutoResearch-specific domain events that the UI controller can consume without knowing OMC internals.

- [ ] **Step 1: Create the event adapter**

```javascript
// src/event-adapter.js
// Translates OMC CompanyEvents → AutoResearch domain events.

// OMC event types we care about:
// - meeting_booked: a meeting room was allocated (stage starting)
// - meeting_chat: a message within an ongoing meeting (producer/critic output)
// - agent_task_update: stage state change
// - tree_update: task tree changed (new stage dispatched)
// - routine_phase: pipeline phase progression
// - state_snapshot: heartbeat / full refresh signal

export class EventAdapter {
  constructor() {
    this.handlers = {
      stage_start: [],
      meeting_message: [],
      gate_decision: [],
      stage_complete: [],
      stage_failed: [],
      director_action: [],
      system_event: [],
    };
  }

  on(eventName, fn) {
    if (!this.handlers[eventName]) this.handlers[eventName] = [];
    this.handlers[eventName].push(fn);
  }

  emit(eventName, data) {
    (this.handlers[eventName] || []).forEach(fn => fn(data));
  }

  // Feed raw OMC events here
  process(omcEvent) {
    const { type, agent, payload } = omcEvent;

    switch (type) {
      case 'meeting_booked':
        this.emit('stage_start', {
          stageId: this._inferStageFromAgent(agent, payload),
          roomId: payload.room_id,
          roomName: payload.room_name,
          participants: payload.participants || [],
        });
        break;

      case 'meeting_chat':
        this.emit('meeting_message', {
          agent: agent,
          role: this._inferRole(agent),
          message: payload.message || payload.content || '',
          roomId: payload.room_id,
        });
        break;

      case 'agent_done':
        // Agent finished its task — could be a gate pass
        this.emit('stage_complete', {
          agent: agent,
          stageId: this._inferStageFromAgent(agent, payload),
          result: payload.result || payload.summary || '',
        });
        break;

      case 'routine_phase':
        this.emit('director_action', {
          phase: payload.phase,
          message: payload.message,
        });
        break;

      case 'tree_update':
      case 'agent_task_update':
        this.emit('system_event', {
          type: type,
          agent: agent,
          payload: payload,
        });
        break;

      case 'state_snapshot':
        // Heartbeat — UI can refresh full state from REST
        this.emit('system_event', { type: 'heartbeat' });
        break;

      default:
        // Forward unknown events as system events for the log panel
        this.emit('system_event', { type, agent, payload });
    }
  }

  // --- Helpers ---

  _inferRole(agentId) {
    if (!agentId) return 'system';
    const id = agentId.toLowerCase();
    if (id.includes('critic') || id.includes('reviewer')) return 'critic';
    if (id.includes('director')) return 'director';
    return 'producer';
  }

  _inferStageFromAgent(agentId, payload) {
    // Map talent IDs to stage numbers
    const TALENT_STAGE_MAP = {
      'topic-refiner': 1,
      'literature-surveyor': 2, 'lit-surveyor': 2,
      'idea-generator': 3, 'idea-gen': 3,
      'methodology-designer': 4, 'method': 4,
      'experiment-designer': 5, 'exp-design': 5,
      'experimentalist': 6,
      'result-analyst': 7, 'analyst': 7,
      'paper-writer': 8,
      'peer-reviewer': 9, 'reviewer': 9,
    };

    if (agentId && TALENT_STAGE_MAP[agentId.toLowerCase()]) {
      return TALENT_STAGE_MAP[agentId.toLowerCase()];
    }

    // Fall back to payload hints
    if (payload && payload.stage_id) return payload.stage_id;
    if (payload && payload.node_id) return this._stageFromNodeId(payload.node_id);
    return null;
  }

  _stageFromNodeId(nodeId) {
    // OMC node IDs might contain stage hints — parse as needed
    return null;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add src/event-adapter.js
git commit -m "feat: add event adapter translating OMC events to domain events"
```

---

## Task 3: Pipeline Controller

**Files:**
- Create: `src/pipeline-controller.js`

This module owns all UI mutations: creating meeting cards, updating stage dots, managing the event log, and handling breakpoints. It subscribes to the EventAdapter's domain events.

- [ ] **Step 1: Create the pipeline controller**

```javascript
// src/pipeline-controller.js
// Drives UI state in response to domain events from EventAdapter.

export class PipelineController {
  constructor(adapter) {
    this.adapter = adapter;
    this.currentStage = null;
    this.meetingCards = {}; // stageId → DOM element

    // Subscribe to domain events
    adapter.on('stage_start', (e) => this.handleStageStart(e));
    adapter.on('meeting_message', (e) => this.handleMeetingMessage(e));
    adapter.on('stage_complete', (e) => this.handleStageComplete(e));
    adapter.on('stage_failed', (e) => this.handleStageFailed(e));
    adapter.on('director_action', (e) => this.handleDirectorAction(e));
    adapter.on('system_event', (e) => this.handleSystemEvent(e));
  }

  handleStageStart({ stageId, roomName, participants }) {
    if (!stageId) return;

    // Collapse previous meeting card
    if (this.currentStage && this.meetingCards[this.currentStage]) {
      this.meetingCards[this.currentStage].classList.add('collapsed');
    }

    this.currentStage = stageId;
    setStage(stageId, 'running');
    addEvent('dtag', `Delegating Stage ${stageId}`);
    addEvent('mtag', `Meeting: ${roomName || `Stage ${stageId}`}`);

    // Create meeting card using existing createMeeting() from index.html
    const producerName = this._getProducerName(stageId);
    const initials = this._getInitials(producerName);
    const card = createMeeting(
      `s${stageId}`,
      producerName,
      initials,
      `Stage ${stageId} — ${this._getStageName(stageId)}`
    );
    this.meetingCards[stageId] = card;
  }

  handleMeetingMessage({ agent, role, message }) {
    if (!this.currentStage) return;
    const card = this.meetingCards[this.currentStage];
    if (!card) return;

    if (role === 'producer') {
      const prodEl = card.querySelector(`#prod-s${this.currentStage}`);
      if (prodEl) prodEl.innerHTML = message;
    } else if (role === 'critic') {
      const critEl = card.querySelector(`#crit-s${this.currentStage}`);
      if (critEl) critEl.innerHTML = message;
    }
  }

  handleStageComplete({ stageId, result }) {
    const sid = stageId || this.currentStage;
    if (!sid) return;

    const card = this.meetingCards[sid];
    if (card) {
      const badge = card.querySelector('.meeting-badge');
      if (badge) {
        badge.className = 'meeting-badge concluded';
        badge.textContent = 'Passed';
      }
      card.classList.remove('active');
    }

    setStage(sid, 'done');
    const connector = document.getElementById(`sc-${sid}`);
    if (connector) connector.classList.add('done');
    addEvent('gtag', `PASS — Stage ${sid}`);

    // Check breakpoint
    if (typeof STAGES !== 'undefined' && STAGES[sid - 1] && STAGES[sid - 1].bp) {
      this._triggerBreakpoint(sid, card);
    }
  }

  handleStageFailed({ stageId, reason }) {
    const sid = stageId || this.currentStage;
    if (!sid) return;

    const card = this.meetingCards[sid];
    if (card) {
      const badge = card.querySelector('.meeting-badge');
      if (badge) {
        badge.className = 'meeting-badge rejected';
        badge.textContent = 'Rejected';
      }
      card.classList.remove('active');
      card.classList.add('rejected');
    }

    setStage(sid, 'failed');
    addEvent('gtag', `REJECTED — Stage ${sid}`);
  }

  handleDirectorAction({ phase, message }) {
    addEvent('dtag', message || phase);
    const dirStatus = document.getElementById('dirStatus');
    if (dirStatus) dirStatus.textContent = message || phase;
  }

  handleSystemEvent({ type, agent, payload }) {
    if (type === 'heartbeat') return;
    addEvent('stag', `[${type}] ${agent || ''}: ${JSON.stringify(payload || {}).slice(0, 80)}`);
  }

  _triggerBreakpoint(stageId, card) {
    setStage(stageId, 'paused');
    if (card) card.classList.add('paused');
    addActionBar(card, stageId);
    addEvent('stag', `Breakpoint on Stage ${stageId}. Waiting for user.`);
    const dirStatus = document.getElementById('dirStatus');
    if (dirStatus) dirStatus.textContent = `Paused at Stage ${stageId} — waiting for user`;
  }

  // --- Stage metadata ---

  _getProducerName(stageId) {
    const names = {
      1: 'Topic Refiner', 2: 'Lit. Surveyor', 3: 'Idea Generator',
      4: 'Methodology Designer', 5: 'Experiment Designer',
      6: 'Experimentalist', 7: 'Result Analyst', 8: 'Paper Writer', 9: 'Peer Reviewer',
    };
    return names[stageId] || `Stage ${stageId}`;
  }

  _getInitials(name) {
    return name.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2);
  }

  _getStageName(stageId) {
    const stages = {
      1: 'Topic Refinement', 2: 'Literature Survey', 3: 'Idea Generation',
      4: 'Methodology Design', 5: 'Experiment Design',
      6: 'Auto Experiment', 7: 'Result Analysis', 8: 'Paper Generation', 9: 'Self-Review',
    };
    return stages[stageId] || `Stage ${stageId}`;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add src/pipeline-controller.js
git commit -m "feat: add pipeline controller driving UI from domain events"
```

---

## Task 4: Main Entry Point + Wire Up

**Files:**
- Create: `src/main.js`
- Modify: `index.html`

- [ ] **Step 1: Create main.js entry point**

```javascript
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

  try {
    await client.connect();
    addEvent('stag', 'Connected to OMC backend.');
    document.getElementById('dirStatus').textContent = 'Connected — ready';
  } catch (err) {
    addEvent('stag', `Connection failed: ${err.message}. Running in demo mode.`);
    document.getElementById('dirStatus').textContent = 'Offline — demo mode';
    return false;
  }
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

  addEvent('dtag', `Task accepted. Project: ${result.project_id}`);
  document.getElementById('dirStatus').textContent = 'Pipeline running...';
}

// Expose for HTML onclick/onkeydown
window.launchPipeline = launchPipeline;

// Initialize on load
document.addEventListener('DOMContentLoaded', () => {
  init();
});
```

- [ ] **Step 2: Modify index.html to use module entry point**

In `index.html`, add the module script tag before closing `</body>` and modify the topic submission to call `launchPipeline` when connected:

Replace the existing `startDemo` trigger at the bottom of the `<script>` tag:
```javascript
document.getElementById('topicInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();startDemo()}});
```

With:
```javascript
document.getElementById('topicInput').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey){
    e.preventDefault();
    const topic = document.getElementById('topicInput').value.trim();
    if(topic && window.launchPipeline) {
      window.launchPipeline(topic);
    } else {
      startDemo();
    }
  }
});
```

And add the module import before `</body>`:
```html
<script type="module" src="src/main.js"></script>
```

- [ ] **Step 3: Test both paths**

1. **Without OMC running:** Open `index.html`, type a topic, press Enter. Should fall back to `startDemo()`.
2. **With OMC running:** Start OMC, refresh page. Console should show "Connected to OMC backend." in events. Submitting a topic should call `/api/ceo/task` and show real events streaming in.

- [ ] **Step 4: Commit**

```bash
git add src/main.js index.html
git commit -m "feat: wire frontend to OMC via module entry point with demo fallback"
```

---

## Task 5: OMC Company Configuration (Research Lab)

**Files:**
- Create: `company/config.yaml` (or equivalent OMC company config)
- Create: `company/talents/` directory with talent profiles

This task sets up the OMC "Research Lab" company instance so there are actual talents for the pipeline to dispatch to.

- [ ] **Step 1: Create the Research Lab company config**

Check OMC's existing company config format:
```bash
ls /Users/yuzhengxu/projects/OneManCompany/OneManCompany/company/
cat /Users/yuzhengxu/projects/OneManCompany/OneManCompany/config.yaml
```

Then create `company/config.yaml`:
```yaml
name: "AutoResearch Lab"
description: "Adversarial research pipeline — 9 stages with critic gate"

employees:
  - id: research-director
    role: COO
    hosting: company

  - id: topic-refiner
    role: Engineer
    hosting: company

  - id: literature-surveyor
    role: Engineer
    hosting: company

  - id: idea-generator
    role: Engineer
    hosting: company

  - id: methodology-designer
    role: Engineer
    hosting: company

  - id: experiment-designer
    role: Engineer
    hosting: company

  - id: experimentalist
    role: Engineer
    hosting: company

  - id: result-analyst
    role: Engineer
    hosting: company

  - id: paper-writer
    role: Engineer
    hosting: company

  - id: adversarial-critic
    role: QA
    hosting: company

  - id: peer-reviewer-1
    role: QA
    hosting: company

  - id: peer-reviewer-2
    role: QA
    hosting: company

  - id: peer-reviewer-3
    role: QA
    hosting: company
```

- [ ] **Step 2: Create Research Director talent profile**

Create `company/talents/research-director/profile.yaml`:
```yaml
name: Research Director
nickname: Director
role: Chief Operating Officer
personality: |
  You are the Research Director of an adversarial research pipeline.
  You orchestrate 9 stages of scientific research, dispatching producer agents
  and scheduling adversarial review meetings with the critic.

  Your decisions:
  - PASS: confidence >= 0.6, advance to next stage
  - RETRY: confidence < 0.6, up to 3 retries with critic feedback
  - PIVOT: 3 retries exhausted, fall back 1-2 stages

  You never produce research output yourself. You delegate, review meeting minutes,
  and decide the pipeline flow.
```

- [ ] **Step 3: Create Adversarial Critic talent profile**

Create `company/talents/adversarial-critic/profile.yaml`:
```yaml
name: Adversarial Critic
nickname: Critic
role: QA
personality: |
  You are the Adversarial Critic for a scientific research pipeline.
  Your job is to find flaws, challenge assumptions, and ensure rigor.

  For each stage output you receive, you must:
  1. Identify specific issues with severity (HIGH/MED/LOW)
  2. Assign a confidence score (0.0-1.0) reflecting output quality
  3. Recommend PASS (conf >= 0.6) or REJECT (conf < 0.6)

  You are deliberately adversarial. False positives (passing bad work) are worse
  than false negatives (rejecting good work that can retry).

  Output format:
  - Issues list with severity tags
  - Confidence score with justification
  - PASS or REJECT decision
```

- [ ] **Step 4: Create one producer talent (Topic Refiner) as template**

Create `company/talents/topic-refiner/profile.yaml`:
```yaml
name: Topic Refiner
nickname: Refiner
role: Engineer
personality: |
  You are the Topic Refiner for a scientific research pipeline.
  You take a raw research topic from the user and produce a refined,
  researchable question with:
  - Clear scope boundaries
  - Identified target venue (NeurIPS/ICML/ICLR/ACL etc.)
  - Preliminary related work positioning
  - Success criteria

  Your output goes directly to the Adversarial Critic for review.
  Be specific and falsifiable. Avoid vague claims.
```

- [ ] **Step 5: Commit**

```bash
git add company/
git commit -m "feat: add OMC Research Lab company config with core talent profiles"
```

---

## Task 6: Confidence Visualization from Real Data

**Files:**
- Modify: `src/event-adapter.js`
- Modify: `src/pipeline-controller.js`

The meeting_chat messages from the critic contain confidence scores. We need to parse them and drive the confidence gauge on meeting cards.

- [ ] **Step 1: Add confidence parsing to event adapter**

In `src/event-adapter.js`, add a method to detect gate decisions from critic messages:

```javascript
// Add to EventAdapter class:

_parseGateDecision(message) {
  // Critic messages contain structured output:
  // "Confidence: 0.72" or "confidence_score: 0.72"
  // "Decision: PASS" or "REJECT"
  const confMatch = message.match(/confidence[:\s_]*([0-9.]+)/i);
  const decisionMatch = message.match(/\b(PASS|REJECT)\b/i);

  if (confMatch || decisionMatch) {
    return {
      confidence: confMatch ? parseFloat(confMatch[1]) : null,
      decision: decisionMatch ? decisionMatch[1].toUpperCase() : null,
    };
  }
  return null;
}
```

Update the `meeting_chat` handler to emit `gate_decision` when critic provides a score:

```javascript
case 'meeting_chat':
  const role = this._inferRole(agent);
  this.emit('meeting_message', {
    agent, role,
    message: payload.message || payload.content || '',
    roomId: payload.room_id,
  });

  // Check if this is a gate decision from critic
  if (role === 'critic') {
    const gate = this._parseGateDecision(payload.message || payload.content || '');
    if (gate && gate.decision) {
      if (gate.decision === 'PASS') {
        this.emit('stage_complete', {
          stageId: this._inferStageFromAgent(agent, payload),
          confidence: gate.confidence,
        });
      } else {
        this.emit('stage_failed', {
          stageId: this._inferStageFromAgent(agent, payload),
          confidence: gate.confidence,
        });
      }
    }
  }
  break;
```

- [ ] **Step 2: Add confidence gauge to pipeline controller**

In `src/pipeline-controller.js`, update `handleStageComplete` and `handleStageFailed` to call `addConf()`:

```javascript
handleStageComplete({ stageId, confidence }) {
  const sid = stageId || this.currentStage;
  if (!sid) return;

  const card = this.meetingCards[sid];
  if (card) {
    const badge = card.querySelector('.meeting-badge');
    if (badge) {
      badge.className = 'meeting-badge concluded';
      badge.textContent = 'Passed';
    }
    card.classList.remove('active');

    // Add confidence gauge if score available
    if (confidence != null) {
      const pct = Math.round(confidence * 100);
      addConf(card, pct);
      const confEl = document.getElementById(`c${sid}`);
      if (confEl) confEl.textContent = `${pct}%`;
      addEvent('gtag', `PASS (${pct}%)`);
    } else {
      addEvent('gtag', `PASS — Stage ${sid}`);
    }
  }

  setStage(sid, 'done');
  const connector = document.getElementById(`sc-${sid}`);
  if (connector) connector.classList.add('done');

  if (typeof STAGES !== 'undefined' && STAGES[sid - 1] && STAGES[sid - 1].bp) {
    this._triggerBreakpoint(sid, card);
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add src/event-adapter.js src/pipeline-controller.js
git commit -m "feat: parse critic confidence scores and drive gauge visualization"
```

---

## Task 7: Breakpoint Resume via REST

**Files:**
- Modify: `src/omc-client.js`
- Modify: `src/pipeline-controller.js`
- Modify: `index.html` (action panel approve button)

When the user hits "Approve & Continue" in the action panel at a breakpoint, we need to signal OMC to resume.

- [ ] **Step 1: Add resume endpoint to OmcClient**

In `src/omc-client.js`, add:

```javascript
async resumeAfterBreakpoint(projectId, stageId, userFeedback = '') {
  // Use the followup endpoint to continue the project
  const res = await fetch(`${this.baseUrl}/api/task/${projectId}/followup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      message: userFeedback || `Stage ${stageId} approved. Continue.`,
    }),
  });
  return res.json();
}

async sendOneOnOneMessage(employeeId, message) {
  const res = await fetch(`${this.baseUrl}/api/oneonone/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ employee_id: employeeId, message }),
  });
  return res.json();
}
```

- [ ] **Step 2: Wire action panel approve button**

In `src/pipeline-controller.js`, update `_triggerBreakpoint` to store context and expose a resume method:

```javascript
_triggerBreakpoint(stageId, card) {
  this.pausedStageId = stageId;
  setStage(stageId, 'paused');
  if (card) card.classList.add('paused');
  addActionBar(card, stageId);
  addEvent('stag', `Breakpoint on Stage ${stageId}. Waiting for user.`);
  const dirStatus = document.getElementById('dirStatus');
  if (dirStatus) dirStatus.textContent = `Paused at Stage ${stageId} — waiting for user`;
}

async resumeBreakpoint(feedback = '') {
  if (!this.pausedStageId) return;
  const sid = this.pausedStageId;
  this.pausedStageId = null;

  const card = this.meetingCards[sid];
  if (card) card.classList.remove('paused');

  // Remove action panel
  document.getElementById('action-panel-global')?.remove();

  setStage(sid, 'done');
  addEvent('dtag', `Stage ${sid} approved by user. Proceeding.`);

  // Signal OMC to continue (exposed via window for HTML onclick)
  if (window._omcClient && window._currentProjectId) {
    await window._omcClient.resumeAfterBreakpoint(
      window._currentProjectId, sid, feedback
    );
  }
}
```

- [ ] **Step 3: Expose resumeBreakpoint in main.js**

In `src/main.js`, after creating the controller:

```javascript
// Expose for action panel buttons
window._omcClient = client;
window._controller = controller;
window.resumeBreakpoint = (feedback) => controller.resumeBreakpoint(feedback);
```

- [ ] **Step 4: Modify action panel "Approve" button in index.html**

In the `addActionBar` function in `index.html`, change the approve button handler:

From (existing demo logic):
```javascript
approveBtn.onclick = () => { resolve(); ... }
```

To:
```javascript
approveBtn.onclick = () => {
  if (window.resumeBreakpoint) {
    window.resumeBreakpoint('');
  }
  // Also resolve the demo promise if running in demo mode
  if (typeof resolve === 'function') resolve();
};
```

- [ ] **Step 5: Commit**

```bash
git add src/omc-client.js src/pipeline-controller.js src/main.js index.html
git commit -m "feat: breakpoint resume via REST — approve button signals OMC to continue"
```

---

## Task 8: Connection Status Indicator + Error Handling

**Files:**
- Modify: `index.html` (add connection indicator to header)
- Modify: `src/main.js` (reconnection logic)

- [ ] **Step 1: Add connection status badge to header**

In `index.html`, inside the sidebar header area (near the "Research Director" status), add:

```html
<div class="conn-status" id="connStatus">
  <span class="conn-dot"></span>
  <span class="conn-label">Offline</span>
</div>
```

CSS:
```css
.conn-status { display:flex; align-items:center; gap:6px; font-size:12px; opacity:0.7; }
.conn-dot { width:8px; height:8px; border-radius:50%; background:var(--terracotta); }
.conn-status.connected .conn-dot { background:var(--forest); }
.conn-status.connected .conn-label::after { content:'Connected'; }
.conn-status:not(.connected) .conn-label::after { content:'Offline'; }
```

- [ ] **Step 2: Update main.js to toggle connection status**

```javascript
function setConnectionStatus(connected) {
  const el = document.getElementById('connStatus');
  if (!el) return;
  el.className = connected ? 'conn-status connected' : 'conn-status';
  el.querySelector('.conn-label').textContent = connected ? 'Connected' : 'Offline';
}

// In init():
client.ws.onopen = () => setConnectionStatus(true);
client.ws.onclose = () => setConnectionStatus(false);
```

- [ ] **Step 3: Commit**

```bash
git add index.html src/main.js
git commit -m "feat: add connection status indicator with auto-reconnect feedback"
```

---

## Task 9: End-to-End Integration Test

**Files:** No new files — manual verification.

- [ ] **Step 1: Start OMC with Research Lab config**

```bash
cd /Users/yuzhengxu/projects/OneManCompany/OneManCompany
# Point OMC to the autoresearch company config
COMPANY_DIR=/Users/yuzhengxu/projects/autoresearch/company python -m onemancompany.main
```

Verify: OMC starts, loads the research-director and other talents.

- [ ] **Step 2: Open frontend, verify connection**

Open `index.html` in browser. Verify:
- Connection status shows "Connected" (green dot)
- Events panel shows "Connected to OMC backend"
- Research Director status shows "Connected — ready"

- [ ] **Step 3: Submit a research topic**

Type: "Learned routing mechanisms for sparse attention in long-context transformers"

Verify:
- Task is submitted (events show "Submitting..." then "Task accepted")
- Meeting cards appear as OMC dispatches stages
- Critic messages stream into the right column of cards
- Confidence gauges appear after critic decisions
- If a breakpoint fires, action panel appears

- [ ] **Step 4: Document any OMC event format mismatches**

If OMC's actual event payloads differ from what the adapter expects (e.g., different field names), note them and fix the adapter. This is expected — the adapter is the translation layer and may need tuning based on real event shapes.

- [ ] **Step 5: Commit any fixes**

```bash
git add -u
git commit -m "fix: adjust event adapter for actual OMC payload format"
```

---

## Summary of Integration Architecture

```
┌─────────────┐     WebSocket      ┌─────────────────┐
│  index.html │ ◄──────────────────│  OMC Backend     │
│  (UI)       │     events stream  │  (FastAPI)       │
│             │                    │                  │
│  main.js    │ ────REST POST────► │  /api/ceo/task   │
│  (entry)    │                    │  /api/task/*/followup │
└──────┬──────┘                    └─────────────────┘
       │
       ▼
┌──────────────┐
│ omc-client.js│  WebSocket connect + REST wrappers
└──────┬───────┘
       │ raw OMC events
       ▼
┌──────────────────┐
│ event-adapter.js │  Translate { type, agent, payload } → domain events
└──────┬───────────┘
       │ stage_start, meeting_message, gate_decision, ...
       ▼
┌────────────────────────┐
│ pipeline-controller.js │  Drive UI: meeting cards, stage dots, breakpoints
└────────────────────────┘
```
