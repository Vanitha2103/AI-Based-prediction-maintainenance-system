const motorRows = document.getElementById("motor-rows");
const eventRows = document.getElementById("event-rows");
const searchInput = document.getElementById("motor-search");
const chart = document.getElementById("health-chart");
const context = chart.getContext("2d");
let motors = [];
let events = [];
let selectedHistory = [];
let selectedMotorId = "M-204";
let paused = false;
let dataSource = "simulation";
let requestInProgress = false;
let toastTimeout;

function statusLabel(state) {
  return state === "critical" ? "Critical" : state === "watch" ? "Attention" : "Healthy";
}

function riskScore(motor) {
  return 100 - motor.health;
}

async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers }
  });
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    throw new Error(body?.error || `Request failed (${response.status}).`);
  }
  return body ?? response;
}

function renderFleet(summary) {
  const query = searchInput.value.trim().toLowerCase();
  const filteredMotors = motors.filter((motor) => `${motor.id} ${motor.location}`.toLowerCase().includes(query));
  motorRows.innerHTML = filteredMotors.map((motor) => {
    const fillClass = motor.state === "critical" ? "critical" : motor.state === "watch" ? "watch" : "";
    return `<tr data-motor-id="${motor.id}" class="${motor.id === selectedMotorId ? "selected-row" : ""}" tabindex="0" aria-label="${motor.id}, ${statusLabel(motor.state)}, health ${motor.health}">
      <td><div class="asset-cell"><span class="motor-avatar">◉</span><span class="motor-meta"><strong>${motor.id}</strong><small>${motor.location}</small></span></div></td>
      <td><div class="health-cell"><span>${motor.health}</span><span class="health-track"><i class="health-fill ${fillClass}" style="width:${motor.health}%"></i></span></div></td>
      <td class="reading-cell">${motor.temperature.toFixed(1)}<span class="unit-muted">°C</span></td>
      <td class="reading-cell">${motor.vibration.toFixed(1)}<span class="unit-muted"> mm/s</span></td>
      <td><span class="status-pill status-${motor.state}">${statusLabel(motor.state)}</span></td>
    </tr>`;
  }).join("");
  document.getElementById("empty-search").hidden = filteredMotors.length !== 0;
  document.getElementById("fleet-count").textContent = query
    ? `Showing ${filteredMotors.length} of ${motors.length} motors`
    : `Showing ${motors.length} motors`;
  document.getElementById("metric-motors").innerHTML = `${String(summary.total).padStart(2, "0")} <span class="metric-unit">units</span>`;
  document.getElementById("metric-healthy").innerHTML = `${String(summary.healthy).padStart(2, "0")} <span class="metric-unit">motors</span>`;
  document.getElementById("metric-attention").innerHTML = `${String(summary.watch).padStart(2, "0")} <span class="metric-unit">motors</span>`;
  document.getElementById("metric-critical").innerHTML = `${String(summary.critical).padStart(2, "0")} <span class="metric-unit">motor${summary.critical === 1 ? "" : "s"}</span>`;
  document.getElementById("healthy-percent").textContent = `${summary.total ? Math.round(summary.healthy / summary.total * 100) : 0}%`;
}

function formatEventTime(value) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 60_000));
  return minutes === 0 ? "Just now" : `${minutes} min ago`;
}

function renderEvents() {
  eventRows.innerHTML = events.map((event) => {
    const severityLabel = event.severity === "critical" ? "Critical" : "Attention";
    return `<tr>
      <td><span class="status-pill status-${event.severity}">${severityLabel}</span></td>
      <td><span class="event-asset">${event.motor_id}</span></td>
      <td><span class="event-title">${event.title}</span><span class="event-subtitle">${event.detail}</span></td>
      <td>${event.action}</td>
      <td class="event-time">${formatEventTime(event.created_at)}</td>
      <td><button class="ack-button" type="button" data-event-id="${event.id}">Acknowledge</button></td>
    </tr>`;
  }).join("");
  document.getElementById("empty-events").hidden = events.length !== 0;
  document.getElementById("nav-alert-count").textContent = events.length;
}

function drawChart(motor) {
  const rect = chart.getBoundingClientRect();
  const pixelRatio = window.devicePixelRatio || 1;
  chart.width = Math.max(1, Math.floor(rect.width * pixelRatio));
  chart.height = Math.max(1, Math.floor(rect.height * pixelRatio));
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  const width = rect.width;
  const height = rect.height - 13;
  const values = selectedHistory.length
    ? selectedHistory.map((point) => point.health)
    : [motor.health];
  const points = values.map((value, index) => ({
    x: values.length === 1 ? width : (index / (values.length - 1)) * width,
    y: height - ((value - 35) / 65) * height
  }));
  const yFor = (value) => height - ((value - 35) / 65) * height;
  context.save();
  context.setLineDash([4, 4]);
  context.strokeStyle = "#e0a248";
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(0, yFor(75));
  context.lineTo(width, yFor(75));
  context.stroke();
  context.restore();
  const gradient = context.createLinearGradient(0, 0, 0, height);
  gradient.addColorStop(0, "rgba(39, 161, 122, 0.18)");
  gradient.addColorStop(1, "rgba(39, 161, 122, 0)");
  context.beginPath();
  context.moveTo(points[0].x, height);
  points.forEach((point) => context.lineTo(point.x, point.y));
  context.lineTo(points[points.length - 1].x, height);
  context.closePath();
  context.fillStyle = gradient;
  context.fill();
  context.beginPath();
  points.forEach((point, index) => {
    if (index === 0) context.moveTo(point.x, point.y);
    else context.lineTo(point.x, point.y);
  });
  context.strokeStyle = "#27a17a";
  context.lineWidth = 2;
  context.lineJoin = "round";
  context.lineCap = "round";
  context.stroke();
  const lastPoint = points[points.length - 1];
  context.beginPath();
  context.arc(lastPoint.x, lastPoint.y, 3, 0, Math.PI * 2);
  context.fillStyle = "#fff";
  context.fill();
  context.lineWidth = 2;
  context.strokeStyle = "#27a17a";
  context.stroke();
}

function renderDetail() {
  const motor = motors.find((asset) => asset.id === selectedMotorId);
  if (!motor) return;
  document.getElementById("detail-name").textContent = `Motor ${motor.id}`;
  document.getElementById("detail-location").textContent = `▣ ${motor.location}`;
  document.getElementById("detail-health").textContent = motor.health;
  document.getElementById("health-gauge").style.setProperty("--gauge-value", motor.health);
  const statePill = document.getElementById("detail-status");
  statePill.className = `status-pill status-${motor.state}`;
  statePill.textContent = statusLabel(motor.state);
  document.getElementById("detail-risk-title").textContent = motor.state === "critical" ? "Critical risk detected" : motor.state === "watch" ? "Elevated risk detected" : "Operating normally";
  document.getElementById("detail-risk-copy").textContent = motor.state === "critical" ? "Prompt inspection recommended" : motor.state === "watch" ? "Review condition indicators" : "All condition indicators normal";
  document.getElementById("detail-temp").innerHTML = `${motor.temperature.toFixed(1)}<span>°C</span>`;
  document.getElementById("detail-vibration").innerHTML = `${motor.vibration.toFixed(1)}<span>mm/s</span>`;
  document.getElementById("detail-current").innerHTML = `${motor.current.toFixed(1)}<span>A</span>`;
  document.getElementById("detail-speed").innerHTML = `${motor.speed.toLocaleString()}<span>RPM</span>`;
  const insight = motor.state === "critical"
    ? `Demo model flags a ${riskScore(motor)}% condition risk. Inspect bearings, alignment, and cooling before continued operation.`
    : motor.state === "watch"
      ? `Demo model flags a ${riskScore(motor)}% condition risk. Review this motor during the next planned inspection.`
      : `Demo model estimates ${motor.health}% health. Continue routine monitoring; no immediate action is indicated.`;
  document.getElementById("detail-insight").textContent = insight;
  drawChart(motor);
}

function updatePauseControl() {
  document.body.classList.toggle("is-paused", paused);
  document.getElementById("live-label").textContent = paused ? "Updates paused" : "Live updates";
  const button = document.getElementById("pause-button");
  button.setAttribute("aria-label", paused ? "Resume live updates" : "Pause live updates");
  button.title = paused ? "Resume live updates" : "Pause live updates";
  document.getElementById("pause-icon").innerHTML = paused
    ? '<path d="m7 5 8 5-8 5z"/>'
    : "<path d='M7 5v10M13 5v10'/>";
}

function updateDataSourceControl() {
  const simulationActive = dataSource === "simulation";
  document.getElementById("source-title").textContent = simulationActive ? "Simulation online" : "Live sensor feed";
  document.getElementById("source-subtitle").textContent = simulationActive ? "SQLite · demo readings" : "SQLite · external readings";
  const button = document.getElementById("source-button");
  button.textContent = simulationActive ? "Use live feed" : "Use simulator";
  button.setAttribute("aria-label", simulationActive ? "Switch to external sensor readings" : "Switch to simulated readings");
  button.title = simulationActive ? "Switch to external sensor readings" : "Switch to simulated readings";
}

async function refreshState() {
  if (requestInProgress) return;
  requestInProgress = true;
  try {
    const state = await apiRequest("/api/state");
    motors = state.motors;
    events = state.events;
    paused = state.paused;
    dataSource = state.data_source;
    if (!motors.some((motor) => motor.id === selectedMotorId)) {
      selectedMotorId = motors[0]?.id;
    }
    renderFleet(state.summary);
    renderEvents();
    updatePauseControl();
    updateDataSourceControl();
    await refreshHistory();
  } finally {
    requestInProgress = false;
  }
}

async function refreshHistory() {
  if (!selectedMotorId) return;
  const result = await apiRequest(`/api/motors/${encodeURIComponent(selectedMotorId)}/history`);
  if (result.motor_id === selectedMotorId) {
    selectedHistory = result.history;
    renderDetail();
  }
}

function showToast(message) {
  const toast = document.getElementById("toast");
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(toastTimeout);
  toastTimeout = window.setTimeout(() => toast.classList.remove("visible"), 2400);
}

function showConnectionBanner(message) {
  let banner = document.getElementById("connection-banner");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "connection-banner";
    banner.className = "connection-banner";
    banner.setAttribute("role", "alert");
    document.querySelector(".main-content").prepend(banner);
  }
  banner.replaceChildren();
  const text = document.createElement("span");
  text.textContent = message;
  const link = document.createElement("a");
  link.href = "http://127.0.0.1:8000/";
  link.textContent = "Open MotorWatch";
  link.className = "connection-link";
  banner.append(text, link);
}

function hideConnectionBanner() {
  document.getElementById("connection-banner")?.remove();
}

async function acknowledgeEvent(eventId) {
  await apiRequest(`/api/events/${encodeURIComponent(eventId)}/ack`, { method: "POST", body: "{}" });
  await refreshState();
  showToast("Event acknowledged.");
}

async function exportReport() {
  const response = await fetch("/api/export.csv", { cache: "no-store" });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error || `Report export failed (${response.status}).`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("content-disposition") || "";
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] || "motorwatch-fleet.csv";
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
  showToast("Fleet report exported.");
}

function reportActionError(error) {
  console.error(error);
  showToast(error.message || "Unable to complete the request.");
}

motorRows.addEventListener("click", async (event) => {
  const row = event.target.closest("tr[data-motor-id]");
  if (!row) return;
  selectedMotorId = row.dataset.motorId;
  renderFleet({ total: motors.length, healthy: motors.filter((motor) => motor.state === "healthy").length, watch: motors.filter((motor) => motor.state === "watch").length, critical: motors.filter((motor) => motor.state === "critical").length });
  selectedHistory = [];
  renderDetail();
  try {
    await refreshHistory();
  } catch (error) {
    reportActionError(error);
  }
});
motorRows.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest("tr[data-motor-id]");
  if (!row) return;
  event.preventDefault();
  selectedMotorId = row.dataset.motorId;
  row.click();
});
searchInput.addEventListener("input", () => {
  if (!motors.length) return;
  renderFleet({ total: motors.length, healthy: motors.filter((motor) => motor.state === "healthy").length, watch: motors.filter((motor) => motor.state === "watch").length, critical: motors.filter((motor) => motor.state === "critical").length });
});
document.getElementById("pause-button").addEventListener("click", async () => {
  try {
    const result = await apiRequest("/api/control/pause", {
      method: "POST",
      body: JSON.stringify({ paused: !paused })
    });
    document.getElementById("source-button").addEventListener("click", async () => {
      try {
        const result = await apiRequest("/api/control/source", {
          method: "POST",
          body: JSON.stringify({ source: dataSource === "simulation" ? "external" : "simulation" })
        });
        dataSource = result.data_source;
        updateDataSourceControl();
        showToast(dataSource === "simulation" ? "Simulated telemetry enabled." : "Waiting for external sensor readings.");
      } catch (error) {
        reportActionError(error);
      }
    });
    paused = result.paused;
    updatePauseControl();
    showToast(paused ? "Live simulation paused." : "Live simulation resumed.");
  } catch (error) {
    reportActionError(error);
  }
});
document.getElementById("export-button").addEventListener("click", () => exportReport().catch(reportActionError));
document.getElementById("view-all-button").addEventListener("click", () => {
  searchInput.value = "";
  if (motors.length) {
    renderFleet({ total: motors.length, healthy: motors.filter((motor) => motor.state === "healthy").length, watch: motors.filter((motor) => motor.state === "watch").length, critical: motors.filter((motor) => motor.state === "critical").length });
  }
  showToast("Showing all motors.");
});
eventRows.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-event-id]");
  if (button) acknowledgeEvent(button.dataset.eventId).catch(reportActionError);
});
document.getElementById("clear-events-button").addEventListener("click", async () => {
  try {
    const activeIds = events.map((event) => event.id);
    for (const eventId of activeIds) {
      await apiRequest(`/api/events/${encodeURIComponent(eventId)}/ack`, { method: "POST", body: "{}" });
    }
    await refreshState();
    showToast("All events acknowledged.");
  } catch (error) {
    reportActionError(error);
  }
});
document.querySelectorAll("[data-scroll-to]").forEach((button) => {
  button.addEventListener("click", () => document.getElementById(button.dataset.scrollTo).scrollIntoView({ behavior: "smooth" }));
});
document.querySelectorAll(".nav-link").forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    document.querySelectorAll(".nav-link").forEach((item) => item.classList.remove("active"));
    link.classList.add("active");
    const targetId = link.getAttribute("href").slice(1);
    document.getElementById(targetId).scrollIntoView({ behavior: "smooth", block: "start" });
  });
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    searchInput.focus();
  }
});
window.addEventListener("resize", () => {
  const selected = motors.find((motor) => motor.id === selectedMotorId);
  if (selected) drawChart(selected);
});

document.getElementById("current-date").textContent = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" }).format(new Date());
if (window.location.protocol === "file:") {
  showConnectionBanner("You opened the dashboard file directly. The backend only works from the local app address.");
} else {
  refreshState().then(hideConnectionBanner).catch((error) => {
    console.error(error);
    showConnectionBanner(`Cannot connect to the backend (${error.message}). Start the server with run.bat, then open the local app address.`);
  });
  window.setInterval(() => {
    refreshState().then(hideConnectionBanner).catch((error) => {
      showConnectionBanner(`Backend connection lost (${error.message}). Keep the server running and retry.`);
    });
  }, 2000);
}
