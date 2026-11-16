"use strict";

/* ------------------------------------------------------------------ */
/* Configuration and state                                            */
/* ------------------------------------------------------------------ */

const API = {
  session: "/api/dashboard/session",
  login: "/api/dashboard/login",
  logout: "/api/dashboard/logout",
  agents: "/api/dashboard/agents",
  agentTasks: (id) => `/api/dashboard/agents/${encodeURIComponent(id)}/tasks`,
  dashboardDisconnect: (id) =>
    `/api/dashboard/agents/${encodeURIComponent(id)}/disconnect`,
  activity: "/api/dashboard/activity?limit=300",
  events: "/api/dashboard/events",
  connect: (name) => `/connect/${encodeURIComponent(name)}`,
  tasks: "/api/tasks",
  task: (id) => `/api/tasks/${encodeURIComponent(id)}`,
};

const CONNECTIONS_KEY = "agent-playground.connections.v1";
const SELECTED_KEY = "agent-playground.selected.v1";

const STATUS_LABELS = {
  pending: "Pending",
  in_progress: "In Progress",
  completed: "Completed",
};

const PRIORITY_LABELS = {
  low: "Low",
  medium: "Medium",
  high: "High",
};

const KANBAN_COLUMNS = [
  { status: "pending", label: "Pending" },
  { status: "in_progress", label: "In Progress" },
  { status: "completed", label: "Completed" },
];

const PRIORITY_ORDER = { high: 0, medium: 1, low: 2 };
const CARD_DESCRIPTION_LIMIT = 256;

const state = {
  dashboardActive: false,
  agents: [],
  managed: [],
  selectedId: null,
  tasks: [],
  activity: [],
  activityAgent: "",
  activityResult: "",
  activeTab: "tasks",
  loadingTasks: false,
  taskReloadTimer: null,
  eventSource: null,
  sessionCheckTimer: null,
  modalHandler: null,
  modalFields: [],
  modalBusy: false,
  detailTaskId: null,
};

const dom = {};

/* ------------------------------------------------------------------ */
/* Tiny DOM helpers                                                   */
/* ------------------------------------------------------------------ */

function h(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function makeOption(value, label, selected = false) {
  const option = h("option", null, label);
  option.value = value;
  option.selected = selected;
  return option;
}

function byId(id) {
  return document.getElementById(id);
}

/* ------------------------------------------------------------------ */
/* API and errors                                                     */
/* ------------------------------------------------------------------ */

class ApiError extends Error {
  constructor(message, status = 0, data = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

function readableError(data, status) {
  if (data && Array.isArray(data.detail)) {
    return data.detail
      .map((item) => {
        const location = Array.isArray(item.loc) ? item.loc.slice(1).join(".") : "";
        return `${location ? location + ": " : ""}${item.msg || "invalid value"}`;
      })
      .join("; ");
  }
  if (data && typeof data.detail === "string") return data.detail;
  if (data && data.detail) return JSON.stringify(data.detail);
  return `Request failed (HTTP ${status})`;
}

async function api(path, { method = "GET", body, token } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError("Network error. Check that the server is reachable.");
  }

  if (response.status === 204) return null;

  const raw = await response.text();
  let data = null;
  if (raw) {
    try {
      data = JSON.parse(raw);
    } catch {
      data = { detail: raw.slice(0, 300) };
    }
  }

  if (!response.ok) {
    throw new ApiError(readableError(data, response.status), response.status, data);
  }
  return data;
}

function toast(message, kind = "info", timeout = 4500) {
  const node = h("div", `toast ${kind}`, message);
  node.setAttribute("role", kind === "error" ? "alert" : "status");
  dom.toastRegion.append(node);
  while (dom.toastRegion.children.length > 4) {
    dom.toastRegion.firstElementChild.remove();
  }
  window.setTimeout(() => node.remove(), timeout);
}

/* ------------------------------------------------------------------ */
/* Formatting                                                         */
/* ------------------------------------------------------------------ */

const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

const timeFormatter = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

function formatDateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : dateTimeFormatter.format(date);
}

function formatTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : timeFormatter.format(date);
}

function shortId(id) {
  return id ? String(id).slice(-8) : "—";
}

function expiryInfo(iso) {
  const timestamp = new Date(iso).getTime();
  if (!Number.isFinite(timestamp)) return { label: "unknown expiry", expired: false };
  const remaining = timestamp - Date.now();
  if (remaining <= 0) return { label: "expired", expired: true };

  const minutes = Math.floor(remaining / 60000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  let label;
  if (days >= 1) label = `${days}d ${hours % 24}h left`;
  else if (hours >= 1) label = `${hours}h ${minutes % 60}m left`;
  else label = `${Math.max(1, minutes)}m left`;
  return { label, expired: false };
}

/* ------------------------------------------------------------------ */
/* Managed connections (browser-created, kept in sessionStorage)      */
/* ------------------------------------------------------------------ */

function readManagedConnections() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(CONNECTIONS_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item) =>
        item &&
        typeof item.connection_id === "string" &&
        typeof item.token === "string" &&
        item.token
    );
  } catch {
    return [];
  }
}

function saveManagedConnections() {
  try {
    sessionStorage.setItem(CONNECTIONS_KEY, JSON.stringify(state.managed));
  } catch {
    // Storage can be disabled; the connection still works for this page load.
  }
}

function managedConnection(id) {
  if (!id) return null;
  return state.managed.find((item) => item.connection_id === id) || null;
}

function rememberConnection(data) {
  state.managed = state.managed.filter(
    (item) => item.connection_id !== data.connection_id
  );
  state.managed.push({
    connection_id: data.connection_id,
    agent_name: data.agent_name,
    token: data.token,
    created_at: data.created_at,
    expires_at: data.expires_at,
  });
  saveManagedConnections();
}

function forgetConnection(id) {
  state.managed = state.managed.filter((item) => item.connection_id !== id);
  saveManagedConnections();
}

/* ------------------------------------------------------------------ */
/* Boot and dashboard session                                         */
/* ------------------------------------------------------------------ */

async function init() {
  cacheDom();
  bindEvents();
  renderAgentList();
  renderHeader();
  renderTasks();
  renderActivityFilters();
  renderActivity();

  try {
    const session = await api(API.session);
    if (session.authenticated) {
      await enterDashboard();
    } else {
      showLogin();
    }
  } catch (error) {
    showLogin(error.message);
  }

  window.setInterval(tick, 30000);
}

function cacheDom() {
  dom.loginView = byId("login-view");
  dom.appView = byId("app-view");
  dom.loginForm = byId("login-form");
  dom.password = byId("dashboard-password");
  dom.loginError = byId("login-error");
  dom.loginSubmit = byId("login-submit");
  dom.newAgentBtn = byId("new-agent-btn");
  dom.agentCount = byId("agent-count");
  dom.agentList = byId("agent-list");
  dom.lockBtn = byId("lock-btn");
  dom.selectedName = byId("selected-name");
  dom.selectedMeta = byId("selected-meta");
  dom.selectedExpiry = byId("selected-expiry");
  dom.manageBadge = byId("manage-badge");
  dom.disconnectBtn = byId("disconnect-btn");
  dom.tabTasks = byId("tab-tasks");
  dom.tabActivity = byId("tab-activity");
  dom.taskTabCount = byId("task-tab-count");
  dom.tasksPanel = byId("tasks-panel");
  dom.activityPanel = byId("activity-panel");
  dom.taskSummary = byId("task-summary");
  dom.taskContext = byId("task-context");
  dom.readonlyNote = byId("readonly-note");
  dom.readonlyConnectBtn = byId("readonly-connect-btn");
  dom.refreshTasksBtn = byId("refresh-tasks-btn");
  dom.newTaskBtn = byId("new-task-btn");
  dom.tasksContainer = byId("tasks-container");
  dom.activityCount = byId("activity-count");
  dom.activityAgentFilter = byId("activity-agent-filter");
  dom.activityResultFilter = byId("activity-result-filter");
  dom.refreshActivityBtn = byId("refresh-activity-btn");
  dom.activityBody = byId("activity-body");
  dom.activityEmpty = byId("activity-empty");
  dom.modalBackdrop = byId("modal-backdrop");
  dom.modalTitle = byId("modal-title");
  dom.modalForm = byId("modal-form");
  dom.modalFields = byId("modal-fields");
  dom.modalError = byId("modal-error");
  dom.modalSubmit = byId("modal-submit");
  dom.modalCancel = byId("modal-cancel");
  dom.modalClose = byId("modal-close");
  dom.taskDetailBackdrop = byId("task-detail-backdrop");
  dom.taskDetailTitle = byId("task-detail-title");
  dom.taskDetailBadges = byId("task-detail-badges");
  dom.taskDetailId = byId("task-detail-id");
  dom.taskDetailDescription = byId("task-detail-description");
  dom.taskDetailCreated = byId("task-detail-created");
  dom.taskDetailUpdated = byId("task-detail-updated");
  dom.taskDetailReadonly = byId("task-detail-readonly");
  dom.taskDetailEdit = byId("task-detail-edit");
  dom.taskDetailDelete = byId("task-detail-delete");
  dom.taskDetailClose = byId("task-detail-close");
  dom.taskDetailFooterClose = byId("task-detail-footer-close");
  dom.toastRegion = byId("toast-region");
}

function showLogin(message = "") {
  state.dashboardActive = false;
  closeEvents();
  closeTaskDetail();
  dom.appView.classList.add("hidden");
  dom.loginView.classList.remove("hidden");
  dom.loginError.textContent = message;
  window.setTimeout(() => dom.password.focus(), 60);
}

async function enterDashboard() {
  dom.loginView.classList.add("hidden");
  dom.appView.classList.remove("hidden");
  state.dashboardActive = true;
  state.managed = readManagedConnections();
  if (state.selectedId === null) {
    const saved = sessionStorage.getItem(SELECTED_KEY);
    if (saved) state.selectedId = saved;
  }

  const agentsOk = await refreshAgents();
  if (!agentsOk) return;
  await refreshActivity();

  const activeIds = new Set(state.agents.map((agent) => agent.connection_id));
  let candidate = activeIds.has(state.selectedId) ? state.selectedId : null;
  if (!candidate) {
    const managedActive = state.managed.find((item) =>
      activeIds.has(item.connection_id)
    );
    candidate = managedActive
      ? managedActive.connection_id
      : state.agents[0]?.connection_id || null;
  }
  await selectAgent(candidate);
  openEvents();
}

async function handleLogin(event) {
  event.preventDefault();
  const password = dom.password.value;
  if (!password.trim()) {
    dom.loginError.textContent = "Enter the dashboard password.";
    return;
  }

  dom.loginError.textContent = "";
  dom.loginSubmit.disabled = true;
  dom.loginSubmit.textContent = "Unlocking…";
  try {
    await api(API.login, { method: "POST", body: { password } });
    dom.password.value = "";
    await enterDashboard();
  } catch (error) {
    dom.loginError.textContent =
      error.status === 401 ? "Incorrect dashboard password." : error.message;
  } finally {
    dom.loginSubmit.disabled = false;
    dom.loginSubmit.textContent = "Unlock dashboard";
  }
}

async function lockDashboard() {
  try {
    await api(API.logout, { method: "POST" });
  } catch {
    // Even if the request fails, hide the local dashboard.
  }
  showLogin("Dashboard locked. Unlock again to continue.");
}

/* ------------------------------------------------------------------ */
/* Agents and selection                                               */
/* ------------------------------------------------------------------ */

async function refreshAgents() {
  try {
    const data = await api(API.agents);
    state.agents = Array.isArray(data.agents) ? data.agents : [];

    // Browser-created connections disappear when they expire or are revoked.
    const activeIds = new Set(state.agents.map((agent) => agent.connection_id));
    const previousLength = state.managed.length;
    state.managed = state.managed.filter((item) =>
      activeIds.has(item.connection_id)
    );
    if (state.managed.length !== previousLength) saveManagedConnections();

    if (state.selectedId && !activeIds.has(state.selectedId)) {
      state.selectedId = null;
      sessionStorage.removeItem(SELECTED_KEY);
      state.tasks = [];
      closeTaskDetail();
    }

    renderAgentList();
    renderHeader();
    renderTasks();
    updateCounts();
    return true;
  } catch (error) {
    if (error.status === 401) {
      showLogin("Dashboard session expired. Unlock again.");
    } else {
      toast(error.message, "error");
    }
    return false;
  }
}

function renderAgentList() {
  dom.agentList.replaceChildren();
  dom.agentCount.textContent = String(state.agents.length);

  if (!state.agents.length) {
    dom.agentList.append(
      h(
        "div",
        "agent-empty",
        "No agents connected. Create one here or connect an external agent."
      )
    );
    return;
  }

  for (const agent of state.agents) {
    const selected = agent.connection_id === state.selectedId;
    const manageable = Boolean(managedConnection(agent.connection_id));
    const expiry = expiryInfo(agent.expires_at);

    const item = h("button", `agent-item${selected ? " is-selected" : ""}`);
    item.type = "button";
    item.setAttribute("aria-pressed", selected ? "true" : "false");
    item.title = `${agent.agent_name} · expires ${formatDateTime(agent.expires_at)}`;

    item.append(
      h("span", `status-dot${expiry.expired ? " expired" : ""}`)
    );

    const copy = h("div", "agent-copy");
    const nameLine = h("div", "agent-name-line");
    nameLine.append(h("span", "agent-name", agent.agent_name));
    const tag = h(
      "span",
      `mini-tag${manageable ? "" : " is-readonly"}`,
      manageable ? "editable" : "read-only"
    );
    nameLine.append(tag);
    copy.append(nameLine);

    const stateLabel = expiry.expired ? "Expired" : "Active";
    copy.append(
      h("span", "agent-meta", `${stateLabel} · ${expiry.label}`)
    );
    item.append(copy);

    item.addEventListener("click", () => {
      selectAgent(agent.connection_id);
    });
    dom.agentList.append(item);
  }
}

async function selectAgent(id, { fetchTasks = true } = {}) {
  closeTaskDetail();
  if (!id) {
    state.selectedId = null;
    state.tasks = [];
    state.loadingTasks = false;
    sessionStorage.removeItem(SELECTED_KEY);
    renderAgentList();
    renderHeader();
    renderTasks();
    updateCounts();
    return;
  }

  state.selectedId = id;
  sessionStorage.setItem(SELECTED_KEY, id);
  // Selecting an agent always brings its Task Manager to the foreground.
  if (state.activeTab !== "tasks") switchTab("tasks");
  state.tasks = [];
  state.loadingTasks = fetchTasks;
  renderAgentList();
  renderHeader();
  renderTasks();
  updateCounts();

  if (fetchTasks) await loadTasks();
}

async function loadTasks() {
  const id = state.selectedId;
  if (!id) return;

  const connection = managedConnection(id);
  const usingAgentApi = Boolean(connection);

  state.loadingTasks = true;
  renderTasks();
  try {
    // Browser-managed environments use the agent API itself, exactly like an
    // external agent. External connections are observed through the read-only
    // dashboard endpoint because their tokens are not available here.
    const data = usingAgentApi
      ? await api(API.tasks, { token: connection.token })
      : await api(API.agentTasks(id));
    if (state.selectedId !== id) return;
    state.tasks = Array.isArray(data.tasks) ? data.tasks : [];
    if (state.detailTaskId) {
      const detailTask = state.tasks.find((task) => task.id === state.detailTaskId);
      if (detailTask) renderTaskDetail(detailTask);
      else closeTaskDetail();
    }
  } catch (error) {
    if (error.status === 401) {
      if (usingAgentApi) {
        closeTaskDetail();
        forgetConnection(id);
        toast(
          "This connection expired or was revoked. Create a new connection to keep editing.",
          "error",
          6500
        );
        renderAgentList();
        renderHeader();
        renderTasks();
        await refreshAgents();
        return;
      }
      showLogin("Dashboard session expired. Unlock again.");
      return;
    }
    toast(error.message, "error");
    if (state.selectedId === id) state.tasks = [];
  } finally {
    if (state.selectedId === id) {
      state.loadingTasks = false;
      renderTasks();
      renderHeader();
      updateCounts();
    }
  }
}

function renderHeader() {
  const agent = state.agents.find(
    (item) => item.connection_id === state.selectedId
  );

  if (!agent) {
    dom.selectedName.textContent = "No agent selected";
    dom.selectedMeta.textContent =
      "Select a connected agent to inspect its isolated task environment.";
    dom.selectedExpiry.classList.add("hidden");
    dom.manageBadge.classList.add("hidden");
    dom.disconnectBtn.classList.add("hidden");
    dom.readonlyNote.classList.add("hidden");
    dom.newTaskBtn.disabled = true;
    dom.refreshTasksBtn.disabled = true;
    dom.taskSummary.textContent = "";
    dom.taskContext.textContent = "No environment selected.";
    return;
  }

  const manageable = Boolean(managedConnection(agent.connection_id));
  const expiry = expiryInfo(agent.expires_at);

  dom.selectedName.textContent = agent.agent_name;
  dom.selectedMeta.textContent = `connection ${shortId(
    agent.connection_id
  )} · created ${formatDateTime(agent.created_at)}`;

  dom.selectedExpiry.textContent = expiry.label;
  dom.selectedExpiry.title = `Token expires ${formatDateTime(agent.expires_at)}`;
  dom.selectedExpiry.classList.remove("hidden");
  dom.selectedExpiry.classList.toggle("is-expired", expiry.expired);

  dom.manageBadge.textContent = manageable ? "Editable here" : "Read-only";
  dom.manageBadge.classList.remove("hidden");
  dom.manageBadge.classList.toggle("pill-manage", manageable);
  dom.manageBadge.classList.toggle("pill-readonly", !manageable);

  dom.disconnectBtn.classList.remove("hidden");
  dom.readonlyNote.classList.toggle("hidden", manageable);

  dom.newTaskBtn.disabled = !manageable;
  dom.refreshTasksBtn.disabled = false;
  dom.taskContext.textContent = `Isolated environment · ${state.tasks.length} task${
    state.tasks.length === 1 ? "" : "s"
  }`;
  dom.taskSummary.textContent = manageable
    ? "Changes go through the agent API with this connection's token."
    : "Read-only view. External agents own their API credentials.";
}

async function tick() {
  if (!state.dashboardActive) return;
  renderAgentList();
  renderHeader();

  const anyExpired = state.agents.some(
    (agent) => new Date(agent.expires_at).getTime() <= Date.now()
  );
  if (anyExpired) await refreshAgents();
}

function updateCounts() {
  dom.agentCount.textContent = String(state.agents.length);
  dom.taskTabCount.textContent = String(
    state.selectedId ? state.tasks.length : 0
  );
}

/* ------------------------------------------------------------------ */
/* Task rendering and mutations                                       */
/* ------------------------------------------------------------------ */

function renderTasks() {
  dom.tasksContainer.replaceChildren();
  updateCounts();

  const agent = state.agents.find(
    (item) => item.connection_id === state.selectedId
  );

  if (!agent) {
    const noAgents = state.agents.length === 0;
    emptyState(dom.tasksContainer, {
      title: noAgents ? "No agents connected yet" : "No agent selected",
      message: noAgents
        ? "Create a connection in this browser, or run POST /connect/{name} from an external agent. Each connection gets its own task environment."
        : "Choose an agent in the sidebar to inspect its isolated task manager.",
      actionLabel: "New agent connection",
      onAction: openConnectModal,
    });
    return;
  }

  if (state.loadingTasks) {
    const loading = h("div", "loading-state");
    loading.append(h("span", "spinner"), h("span", null, "Loading tasks…"));
    dom.tasksContainer.append(loading);
    return;
  }

  const fragment = document.createDocumentFragment();

  for (const column of KANBAN_COLUMNS) {
    const columnTasks = state.tasks
      .filter((task) => task.status === column.status)
      .sort(compareTasks);

    const columnEl = h("section", "kanban-column");
    columnEl.dataset.status = column.status;
    columnEl.setAttribute("aria-label", `${column.label} tasks`);

    const head = h("header", "kanban-head");
    const titleWrap = h("div", "kanban-title");
    titleWrap.append(
      h("span", `kanban-dot ${column.status}`),
      h("h3", null, column.label)
    );
    head.append(titleWrap, h("span", "kanban-count", String(columnTasks.length)));
    columnEl.append(head);

    const list = h("div", "kanban-cards");
    if (columnTasks.length) {
      for (const task of columnTasks) list.append(taskCard(task));
    } else {
      list.append(h("div", "kanban-empty", "No tasks in this stage."));
    }
    columnEl.append(list);
    fragment.append(columnEl);
  }

  dom.tasksContainer.append(fragment);
}

function compareTasks(a, b) {
  const priority = (PRIORITY_ORDER[a.priority] ?? 9) - (PRIORITY_ORDER[b.priority] ?? 9);
  if (priority !== 0) return priority;
  return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
}

function previewDescription(text) {
  const normalized = String(text || "").replace(/\s+/g, " ").trim();
  if (!normalized) return "No description.";
  if (normalized.length <= CARD_DESCRIPTION_LIMIT) return normalized;
  return `${normalized.slice(0, CARD_DESCRIPTION_LIMIT).trimEnd()}...`;
}

function taskCard(task) {
  const card = h("article", "task-card");
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.setAttribute("aria-label", `Open task: ${task.title}`);

  const head = h("div", "task-card-head");
  head.append(h("h3", "task-title", task.title));
  head.append(
    h(
      "span",
      `task-priority priority-${task.priority}`,
      PRIORITY_LABELS[task.priority] || task.priority
    )
  );
  card.append(head);

  const description = previewDescription(task.description);
  card.append(
    h(
      "p",
      `task-desc${description === "No description." ? " is-empty" : ""}`,
      description
    )
  );

  const open = () => openTaskDetail(task);
  card.addEventListener("click", open);
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      open();
    }
  });

  return card;
}

function openTaskDetail(task) {
  const current = state.tasks.find((item) => item.id === task.id) || task;
  state.detailTaskId = current.id;
  renderTaskDetail(current);
  dom.taskDetailBackdrop.classList.remove("hidden");
  window.setTimeout(() => dom.taskDetailClose.focus(), 40);
}

function renderTaskDetail(task) {
  const manageable = Boolean(managedConnection(state.selectedId));

  dom.taskDetailTitle.textContent = task.title;
  dom.taskDetailBadges.replaceChildren(
    h(
      "span",
      `task-priority priority-${task.priority}`,
      PRIORITY_LABELS[task.priority] || task.priority
    ),
    h(
      "span",
      `badge status-${task.status}`,
      STATUS_LABELS[task.status] || task.status
    )
  );

  dom.taskDetailId.textContent = `ID: ${task.id}`;

  const description = (task.description || "").trim();
  dom.taskDetailDescription.textContent = description || "No description.";
  dom.taskDetailDescription.classList.toggle("is-empty", !description);

  dom.taskDetailCreated.textContent = formatDateTime(task.created_at);
  dom.taskDetailUpdated.textContent = formatDateTime(task.updated_at);

  dom.taskDetailEdit.classList.toggle("hidden", !manageable);
  dom.taskDetailDelete.classList.toggle("hidden", !manageable);
  dom.taskDetailReadonly.classList.toggle("hidden", manageable);
}

function closeTaskDetail() {
  if (!dom.taskDetailBackdrop) return;
  dom.taskDetailBackdrop.classList.add("hidden");
  state.detailTaskId = null;
}

function editTaskFromDetail() {
  const task = state.tasks.find((item) => item.id === state.detailTaskId);
  if (!task) {
    closeTaskDetail();
    return;
  }
  closeTaskDetail();
  openTaskModal(task);
}

async function deleteTaskFromDetail() {
  const task = state.tasks.find((item) => item.id === state.detailTaskId);
  if (!task) {
    closeTaskDetail();
    return;
  }
  if (await deleteTask(task)) closeTaskDetail();
}

function replaceTask(updated) {
  const index = state.tasks.findIndex((task) => task.id === updated.id);
  if (index >= 0) state.tasks[index] = updated;
  else state.tasks.unshift(updated);
  renderTasks();
  renderHeader();
  updateCounts();
}

async function deleteTask(task) {
  const connection = managedConnection(state.selectedId);
  if (!connection) return false;
  if (!window.confirm(`Delete "${task.title}"? This cannot be undone.`)) return false;

  try {
    await api(API.task(task.id), {
      method: "DELETE",
      token: connection.token,
    });
    state.tasks = state.tasks.filter((item) => item.id !== task.id);
    renderTasks();
    renderHeader();
    updateCounts();
    toast("Task deleted.", "success");
    return true;
  } catch (error) {
    handleAgentError(error);
    return false;
  }
}

function handleAgentError(error) {
  if (error.status === 401) {
    closeTaskDetail();
    const id = state.selectedId;
    if (id) forgetConnection(id);
    toast(
      "This connection expired or was revoked. Create a new connection to keep editing.",
      "error",
      6500
    );
    renderAgentList();
    renderHeader();
    renderTasks();
    refreshAgents();
    return;
  }
  if (error.status === 404) {
    closeTaskDetail();
    toast("That task no longer exists.", "error");
    loadTasks();
    return;
  }
  toast(error.message, "error", 6500);
}

function emptyState(container, { title, message, actionLabel, onAction }) {
  const box = h("div", "empty-state");
  box.append(h("div", "empty-icon", "✦"));
  box.append(h("h3", null, title));
  box.append(h("p", null, message));
  if (actionLabel && onAction) {
    const button = h("button", "btn btn-primary btn-sm", actionLabel);
    button.type = "button";
    button.addEventListener("click", onAction);
    box.append(button);
  }
  container.append(box);
}

async function disconnectSelected() {
  const agent = state.agents.find(
    (item) => item.connection_id === state.selectedId
  );
  if (!agent) return;
  if (
    !window.confirm(
      `Disconnect "${agent.agent_name}"? Its token is revoked immediately. Tasks are preserved.`
    )
  ) {
    return;
  }

  try {
    await api(API.dashboardDisconnect(agent.connection_id), { method: "POST" });
  } catch (error) {
    if (error.status === 401) {
      showLogin("Dashboard session expired. Unlock again.");
      return;
    }
    if (error.status === 404) {
      toast("That agent is no longer connected.", "error");
      await refreshAgents();
      return;
    }
    toast(error.message, "error", 6500);
    return;
  }

  forgetConnection(agent.connection_id);
  closeTaskDetail();
  if (state.selectedId === agent.connection_id) {
    state.selectedId = null;
    state.tasks = [];
    state.loadingTasks = false;
    sessionStorage.removeItem(SELECTED_KEY);
  }

  toast("Agent disconnected. Its tasks were preserved.", "success");

  const agentsOk = await refreshAgents();
  if (!agentsOk) return;
  await refreshActivity();

  const nextManaged = state.managed.find((item) =>
    state.agents.some((candidate) => candidate.connection_id === item.connection_id)
  );
  const next = nextManaged?.connection_id || state.agents[0]?.connection_id || null;
  await selectAgent(next);
}

/* ------------------------------------------------------------------ */
/* Modals                                                             */
/* ------------------------------------------------------------------ */

function openModal({ title, submitLabel = "Save", description, fields, onSubmit }) {
  state.modalHandler = onSubmit;
  state.modalFields = fields;
  state.modalBusy = false;

  dom.modalTitle.textContent = title;
  dom.modalFields.replaceChildren();
  dom.modalError.textContent = "";
  dom.modalSubmit.textContent = submitLabel;
  dom.modalSubmit.disabled = false;

  if (description) {
    const intro = h("p", "muted small");
    intro.style.margin = "0 0 16px";
    intro.style.lineHeight = "1.6";
    intro.textContent = description;
    dom.modalFields.append(intro);
  }

  for (const field of fields) {
    const label = h("label", "field");
    label.append(h("span", null, field.label));

    let input;
    if (field.type === "textarea") {
      input = h("textarea");
      input.rows = 4;
    } else if (field.type === "select") {
      input = h("select");
      for (const option of field.options) {
        input.append(
          makeOption(option.value, option.label, option.value === field.value)
        );
      }
    } else {
      input = h("input");
      input.type = field.type || "text";
    }

    input.name = field.name;
    if (field.value !== undefined && field.value !== null) input.value = field.value;
    if (field.placeholder) input.placeholder = field.placeholder;
    if (field.required) input.required = true;
    if (field.maxLength) input.maxLength = field.maxLength;
    if (field.type !== "select" && field.type !== "textarea" && !input.value) {
      input.autocomplete = "off";
    }

    label.append(input);
    if (field.help) label.append(h("small", "field-help", field.help));
    dom.modalFields.append(label);
  }

  dom.modalBackdrop.classList.remove("hidden");
  window.setTimeout(() => {
    const first = dom.modalFields.querySelector("input, textarea, select");
    if (first) first.focus();
  }, 40);
}

function closeModal() {
  if (state.modalBusy) return;
  dom.modalBackdrop.classList.add("hidden");
  dom.modalError.textContent = "";
  dom.modalSubmit.textContent = "Save";
  dom.modalSubmit.disabled = false;
  state.modalHandler = null;
  state.modalFields = [];
}

async function submitModal(event) {
  event.preventDefault();
  if (!state.modalHandler || state.modalBusy) return;

  const values = {};
  for (const field of state.modalFields) {
    const input = dom.modalForm.elements.namedItem(field.name);
    if (!input) continue;
    values[field.name] = input.value;
    if (field.required && !String(input.value).trim()) {
      dom.modalError.textContent = `${field.label} is required.`;
      input.focus();
      return;
    }
  }

  state.modalBusy = true;
  dom.modalError.textContent = "";
  const originalLabel = dom.modalSubmit.textContent;
  dom.modalSubmit.disabled = true;
  dom.modalSubmit.textContent = "Working…";

  try {
    await state.modalHandler(values);
    state.modalBusy = false;
    dom.modalSubmit.textContent = originalLabel;
    dom.modalSubmit.disabled = false;
    dom.modalBackdrop.classList.add("hidden");
    dom.modalError.textContent = "";
    state.modalHandler = null;
    state.modalFields = [];
  } catch (error) {
    dom.modalError.textContent = error.message;
  } finally {
    state.modalBusy = false;
    dom.modalSubmit.disabled = false;
    dom.modalSubmit.textContent = originalLabel;
  }
}

function openConnectModal() {
  openModal({
    title: "New agent connection",
    submitLabel: "Create connection",
    description:
      "Creates a new, isolated task environment and returns a 24-hour token. The token stays in this browser tab so you can manage the connection; external agents keep their own token.",
    fields: [
      {
        name: "name",
        label: "Agent name",
        placeholder: "MyAssistant",
        required: true,
        maxLength: 50,
        help: "Letters, digits, spaces, dots, dashes, underscores, parentheses and apostrophes.",
      },
    ],
    onSubmit: async (values) => {
      const name = values.name.trim();
      if (!name) throw new Error("Agent name is required.");
      const data = await api(API.connect(name), { method: "POST" });
      rememberConnection(data);
      await refreshAgents();
      await selectAgent(data.connection_id);
      toast(
        `Connected as ${data.agent_name}. Token valid until ${formatDateTime(
          data.expires_at
        )}.`,
        "success",
        6000
      );
    },
  });
}

function openTaskModal(task) {
  closeTaskDetail();
  const editing = Boolean(task);
  openModal({
    title: editing ? "Edit task" : "New task",
    submitLabel: editing ? "Save changes" : "Create task",
    fields: [
      {
        name: "title",
        label: "Title",
        value: editing ? task.title : "",
        placeholder: "Write the weekly report",
        required: true,
        maxLength: 200,
      },
      {
        name: "description",
        label: "Description",
        type: "textarea",
        value: editing ? task.description : "",
        placeholder: "Optional details",
        maxLength: 5000,
      },
      {
        name: "priority",
        label: "Priority",
        type: "select",
        value: editing ? task.priority : "medium",
        options: ["low", "medium", "high"].map((value) => ({
          value,
          label: PRIORITY_LABELS[value],
        })),
      },
      {
        name: "status",
        label: "Status",
        type: "select",
        value: editing ? task.status : "pending",
        options: ["pending", "in_progress", "completed"].map((value) => ({
          value,
          label: STATUS_LABELS[value],
        })),
      },
    ],
    onSubmit: async (values) => {
      const connection = managedConnection(state.selectedId);
      if (!connection) {
        throw new Error("This environment is read-only in this browser.");
      }
      const payload = {
        title: values.title.trim(),
        description: values.description.trim(),
        priority: values.priority,
        status: values.status,
      };
      if (!payload.title) throw new Error("Title is required.");

      if (editing) {
        const updated = await api(API.task(task.id), {
          method: "PATCH",
          body: payload,
          token: connection.token,
        });
        replaceTask(updated);
        toast("Task updated.", "success");
      } else {
        const created = await api(API.tasks, {
          method: "POST",
          body: payload,
          token: connection.token,
        });
        state.tasks.unshift(created);
        renderTasks();
        renderHeader();
        updateCounts();
        toast("Task created.", "success");
      }
    },
  });
}

/* ------------------------------------------------------------------ */
/* Activity log                                                       */
/* ------------------------------------------------------------------ */

async function refreshActivity() {
  try {
    const data = await api(API.activity);
    state.activity = Array.isArray(data.entries) ? data.entries : [];
    renderActivityFilters();
    renderActivity();
  } catch (error) {
    if (error.status === 401) {
      showLogin("Dashboard session expired. Unlock again.");
      return;
    }
    toast(error.message, "error");
  }
}

function renderActivityFilters() {
  const previous = dom.activityAgentFilter.value || state.activityAgent;
  const names = new Map();
  for (const agent of state.agents) {
    names.set(agent.connection_id, agent.agent_name);
  }
  for (const entry of state.activity) {
    if (entry.connection_id && !names.has(entry.connection_id)) {
      names.set(entry.connection_id, entry.agent_name);
    }
  }
  const hasAnonymous = state.activity.some((entry) => !entry.connection_id);

  dom.activityAgentFilter.replaceChildren();
  dom.activityAgentFilter.append(makeOption("", "All agents"));

  for (const [id, name] of names) {
    dom.activityAgentFilter.append(makeOption(id, name));
  }
  if (hasAnonymous) {
    dom.activityAgentFilter.append(
      makeOption("__anonymous__", "Unauthenticated")
    );
  }

  if (previous === "__anonymous__" && !hasAnonymous) {
    dom.activityAgentFilter.value = "";
  } else if (
    previous &&
    Array.from(dom.activityAgentFilter.options).some(
      (option) => option.value === previous
    )
  ) {
    dom.activityAgentFilter.value = previous;
  } else {
    dom.activityAgentFilter.value = "";
  }
  state.activityAgent = dom.activityAgentFilter.value;

  dom.activityResultFilter.value = state.activityResult;
}

function renderActivity() {
  const filtered = state.activity.filter((entry) => {
    if (state.activityAgent === "__anonymous__") {
      if (entry.connection_id) return false;
    } else if (state.activityAgent) {
      if (entry.connection_id !== state.activityAgent) return false;
    }
    if (state.activityResult === "success" && !entry.success) return false;
    if (state.activityResult === "failure" && entry.success) return false;
    return true;
  });

  dom.activityBody.replaceChildren();
  for (const entry of filtered) {
    dom.activityBody.append(activityRow(entry));
  }

  dom.activityCount.textContent = `${filtered.length} entr${
    filtered.length === 1 ? "y" : "ies"
  }`;
  dom.activityEmpty.classList.toggle("hidden", filtered.length > 0);
}

function activityRow(entry) {
  const row = h("tr", entry.success ? null : "failed");

  const timeCell = h("td", "cell-time", formatTime(entry.timestamp));
  timeCell.title = formatDateTime(entry.timestamp);
  row.append(timeCell);

  const agentCell = h("td", "cell-agent", entry.agent_name);
  agentCell.title = entry.connection_id
    ? `connection ${entry.connection_id}`
    : "no valid token";
  row.append(agentCell);

  const actionCell = h("td", "cell-action");
  actionCell.append(
    h("span", `method ${entry.method.toLowerCase()}`, entry.method),
    h("span", "cell-path", entry.path)
  );
  row.append(actionCell);

  const summaryText = entry.request_summary?.trim() || "—";
  const summaryCell = h("td", "cell-summary", summaryText);
  summaryCell.title = summaryText;
  row.append(summaryCell);

  const resultCell = h("td");
  resultCell.append(
    h("span", `code ${entry.success ? "ok" : "bad"}`, String(entry.status_code)),
    h("span", "result-text", entry.success ? "success" : "failure")
  );
  row.append(resultCell);

  const durationCell = h(
    "td",
    "cell-duration",
    entry.duration_ms < 1 ? "<1 ms" : `${Math.round(entry.duration_ms)} ms`
  );
  row.append(durationCell);

  return row;
}

/* ------------------------------------------------------------------ */
/* Tabs and events                                                    */
/* ------------------------------------------------------------------ */

function switchTab(tab) {
  state.activeTab = tab;
  const tasksActive = tab === "tasks";

  dom.tabTasks.classList.toggle("is-active", tasksActive);
  dom.tabTasks.setAttribute("aria-selected", tasksActive ? "true" : "false");
  dom.tabActivity.classList.toggle("is-active", !tasksActive);
  dom.tabActivity.setAttribute(
    "aria-selected",
    tasksActive ? "false" : "true"
  );
  dom.tasksPanel.classList.toggle("hidden", !tasksActive);
  dom.activityPanel.classList.toggle("hidden", tasksActive);

  if (!tasksActive) refreshActivity();
}

function openEvents() {
  closeEvents();
  if (!state.dashboardActive) return;

  const source = new EventSource(API.events);
  state.eventSource = source;

  source.onmessage = (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    handleEvent(message.type, message.payload).catch(() => {});
  };

  source.onerror = () => {
    if (!state.dashboardActive || state.sessionCheckTimer) return;
    state.sessionCheckTimer = window.setTimeout(async () => {
      state.sessionCheckTimer = null;
      try {
        const session = await api(API.session);
        if (!session.authenticated) {
          showLogin("Dashboard session expired. Unlock again.");
        }
      } catch {
        // EventSource will keep retrying; transient errors are expected.
      }
    }, 4000);
  };
}

function closeEvents() {
  if (state.sessionCheckTimer) {
    window.clearTimeout(state.sessionCheckTimer);
    state.sessionCheckTimer = null;
  }
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
}

async function handleEvent(type, payload) {
  if (!state.dashboardActive) return;

  if (type === "agent.connected") {
    await refreshAgents();
    return;
  }

  if (type === "agent.disconnected") {
    const connectionId = payload?.connection_id;
    state.agents = state.agents.filter(
      (agent) => agent.connection_id !== connectionId
    );
    if (connectionId) forgetConnection(connectionId);

    if (state.selectedId === connectionId) {
      state.selectedId = null;
      state.tasks = [];
      sessionStorage.removeItem(SELECTED_KEY);
    }
    renderAgentList();
    renderHeader();
    renderTasks();
    updateCounts();
    return;
  }

  if (
    type === "task.created" ||
    type === "task.updated" ||
    type === "task.deleted"
  ) {
    if (payload?.connection_id === state.selectedId) {
      if (state.detailTaskId) {
        if (
          type === "task.updated" &&
          payload.task?.id === state.detailTaskId
        ) {
          renderTaskDetail(payload.task);
        }
        if (
          type === "task.deleted" &&
          payload.task_id === state.detailTaskId
        ) {
          closeTaskDetail();
        }
      }
      scheduleTaskReload();
    }
    return;
  }

  if (type === "activity.created" && payload?.entry) {
    state.activity.unshift(payload.entry);
    state.activity = state.activity.slice(0, 500);
    renderActivityFilters();
    renderActivity();
  }
}

function scheduleTaskReload() {
  if (state.taskReloadTimer) window.clearTimeout(state.taskReloadTimer);
  state.taskReloadTimer = window.setTimeout(() => {
    state.taskReloadTimer = null;
    loadTasks();
  }, 180);
}

/* ------------------------------------------------------------------ */
/* Event bindings                                                     */
/* ------------------------------------------------------------------ */

function bindEvents() {
  dom.loginForm.addEventListener("submit", handleLogin);
  dom.newAgentBtn.addEventListener("click", openConnectModal);
  dom.readonlyConnectBtn.addEventListener("click", openConnectModal);
  dom.lockBtn.addEventListener("click", lockDashboard);
  dom.disconnectBtn.addEventListener("click", disconnectSelected);

  dom.tabTasks.addEventListener("click", () => switchTab("tasks"));
  dom.tabActivity.addEventListener("click", () => switchTab("activity"));

  dom.newTaskBtn.addEventListener("click", () => openTaskModal(null));
  dom.refreshTasksBtn.addEventListener("click", loadTasks);
  dom.refreshActivityBtn.addEventListener("click", refreshActivity);

  dom.activityAgentFilter.addEventListener("change", () => {
    state.activityAgent = dom.activityAgentFilter.value;
    renderActivity();
  });
  dom.activityResultFilter.addEventListener("change", () => {
    state.activityResult = dom.activityResultFilter.value;
    renderActivity();
  });

  dom.modalForm.addEventListener("submit", submitModal);
  dom.modalClose.addEventListener("click", closeModal);
  dom.modalCancel.addEventListener("click", closeModal);
  dom.modalBackdrop.addEventListener("click", (event) => {
    if (event.target === dom.modalBackdrop) closeModal();
  });

  dom.taskDetailClose.addEventListener("click", closeTaskDetail);
  dom.taskDetailFooterClose.addEventListener("click", closeTaskDetail);
  dom.taskDetailBackdrop.addEventListener("click", (event) => {
    if (event.target === dom.taskDetailBackdrop) closeTaskDetail();
  });
  dom.taskDetailEdit.addEventListener("click", editTaskFromDetail);
  dom.taskDetailDelete.addEventListener("click", deleteTaskFromDetail);

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!dom.taskDetailBackdrop.classList.contains("hidden")) {
      closeTaskDetail();
      return;
    }
    if (!dom.modalBackdrop.classList.contains("hidden")) {
      closeModal();
    }
  });
}

document.addEventListener("DOMContentLoaded", init);
