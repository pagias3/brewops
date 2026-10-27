// BrewOps frontend — vanilla JS, no dependencies, no build step.

async function fetchJSON(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

// ---- date filter helpers ----

function rangeQuery() {
  const from = document.getElementById("filter-from").value;
  const to = document.getElementById("filter-to").value;
  const params = new URLSearchParams();
  if (from) params.append("from", from);
  if (to) params.append("to", to);
  const qs = params.toString();
  return qs ? "?" + qs : "";
}

function hasRange() {
  return !!document.getElementById("filter-from").value || !!document.getElementById("filter-to").value;
}

function showFilterMessage(text) {
  const msg = document.getElementById("filter-message");
  msg.textContent = text;
  msg.className = "message";
  if (text) msg.classList.add("error");
}

// ---- dashboard ----

let loadSeq = 0;

function renderDrinkBars(perDrink) {
  const container = document.getElementById("drink-bars");
  container.innerHTML = "";
  const max = Math.max(1, ...perDrink.map((d) => d.count));
  for (const drink of perDrink) {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `
      <span class="bar-label">${drink.label}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${(drink.count / max) * 100}%"></span></span>
      <span class="bar-count">${drink.count}</span>`;
    container.appendChild(row);
  }
}

function renderTimeline(perDay) {
  const svg = document.getElementById("timeline");
  svg.innerHTML = "";
  document.getElementById("timeline-empty").hidden = perDay.length > 0;
  if (perDay.length === 0) return;
  const width = 600;
  const height = 130;
  const max = Math.max(...perDay.map((d) => d.count));
  const barWidth = width / perDay.length;
  perDay.forEach((day, i) => {
    const barHeight = (day.count / max) * (height - 10);
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", i * barWidth);
    rect.setAttribute("y", height - barHeight);
    rect.setAttribute("width", Math.max(0.5, barWidth - 0.6));
    rect.setAttribute("height", barHeight);
    rect.setAttribute("class", "timeline-bar");
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${day.day}: ${day.count} brews`;
    rect.appendChild(title);
    svg.appendChild(rect);
  });
}

function renderMachineCards(healths) {
  const container = document.getElementById("machine-cards");
  container.innerHTML = "";
  for (const m of healths) {
    const card = document.createElement("div");
    card.className = "card";
    const maintenance = m.last_maintenance
      ? `${m.last_maintenance.type} on ${m.last_maintenance.timestamp.slice(0, 10)}`
      : "none on record";
    const errors = m.recent_errors.length
      ? `<p class="errors">Recent errors: ${m.recent_errors
          .map((e) => `${e.error_code || "?"} (${e.timestamp.slice(0, 10)})`)
          .join(", ")}</p>`
      : "";
    const specialty = m.specialty
      ? `<strong>${m.specialty.label}</strong> (${m.specialty.count} of ${m.brew_count} brews, ${Math.round((m.specialty.count / m.brew_count) * 100)}%)`
      : "no specialty yet";
    card.innerHTML = `
      <h3>${m.name}</h3>
      <p class="badge">${m.has_telemetry ? "telemetry" : "manual log"}</p>
      <p>${m.brew_count} brews · last ${m.last_brew ? m.last_brew.slice(0, 16) : "never"}</p>
      <p>Specialty: ${specialty}</p>
      <p>Last maintenance: ${maintenance}</p>
      ${errors}`;
    container.appendChild(card);
  }
}

async function loadDashboard() {
  const seq = ++loadSeq;
  const query = rangeQuery();

  const stats = await fetchJSON("/api/stats" + query);
  if (seq !== loadSeq) return;

  document.getElementById("total-brews").textContent = stats.total_brews;
  document.getElementById("total-brews-label").textContent = hasRange() ? "brews in range" : "brews total";

  const lastDay = stats.per_day[stats.per_day.length - 1];
  document.getElementById("brews-today").textContent = lastDay ? lastDay.count : 0;
  renderDrinkBars(stats.per_drink);
  renderTimeline(stats.per_day);

  const machines = await fetchJSON("/api/machines");
  if (seq !== loadSeq) return;

  document.getElementById("machine-count").textContent = machines.length;
  const healths = await Promise.all(machines.map((m) => fetchJSON(`/api/machines/${m.id}${query}`)));
  if (seq !== loadSeq) return;

  renderMachineCards(healths);
  showFilterMessage("");
}

async function reloadFromFilter() {
  try {
    await loadDashboard();
  } catch (error) {
    showFilterMessage(error.message);
  }
}

// ---- forms ----

function localNow() {
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  return now.toISOString().slice(0, 16); // datetime-local format
}

function fillSelect(select, items, valueKey, labelKey) {
  select.innerHTML = "";
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item[valueKey];
    option.textContent = item[labelKey];
    select.appendChild(option);
  }
}

async function setupForms() {
  const machines = await fetchJSON("/api/machines");
  const drinks = await fetchJSON("/api/drink-types");
  fillSelect(document.getElementById("brew-machine"), machines, "id", "name");
  fillSelect(document.getElementById("brew-drink"), drinks, "name", "label");
  fillSelect(document.getElementById("maintenance-machine"), machines, "id", "name");
  document.getElementById("brew-timestamp").value = localNow();
  document.getElementById("maintenance-timestamp").value = localNow();

  document.getElementById("brew-form").addEventListener("submit", (event) =>
    submitForm(event, "/api/brews", "brew-message", () => ({
      machine_id: Number(document.getElementById("brew-machine").value),
      drink_type: document.getElementById("brew-drink").value,
      timestamp: document.getElementById("brew-timestamp").value,
    }))
  );

  document.getElementById("maintenance-form").addEventListener("submit", (event) =>
    submitForm(event, "/api/maintenance", "maintenance-message", () => ({
      machine_id: Number(document.getElementById("maintenance-machine").value),
      type: document.getElementById("maintenance-type").value,
      timestamp: document.getElementById("maintenance-timestamp").value,
      note: document.getElementById("maintenance-note").value || null,
    }))
  );
}

async function submitForm(event, url, messageId, buildPayload) {
  event.preventDefault();
  const message = document.getElementById(messageId);
  message.textContent = "";
  message.className = "message";
  try {
    await fetchJSON(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload()),
    });
    message.textContent = "Logged.";
    message.classList.add("ok");
    await loadDashboard();
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  }
}

function setupFilter() {
  const inputs = [document.getElementById("filter-from"), document.getElementById("filter-to")];
  inputs.forEach((input) => {
    input.addEventListener("change", () => {
      const from = document.getElementById("filter-from").value;
      const to = document.getElementById("filter-to").value;
      if (from && to && from > to) {
        showFilterMessage("'From' must not be after 'To'");
        return;
      }
      reloadFromFilter();
    });
  });

  document.getElementById("filter-clear").addEventListener("click", () => {
    document.getElementById("filter-from").value = "";
    document.getElementById("filter-to").value = "";
    showFilterMessage("");
    reloadFromFilter();
  });
}

loadDashboard().catch((error) => {
  document.getElementById("total-brews").textContent = "!";
  console.error("Dashboard failed to load:", error);
});
setupForms().catch((error) => console.error("Form setup failed:", error));
setupFilter();

