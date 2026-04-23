(function () {
  const catalog = JSON.parse(document.getElementById("api3000-command-catalog").textContent || "{}");
  const form = document.getElementById("api3000-form");
  const commandSelect = document.getElementById("command");
  const paramsContainer = document.getElementById("command-params");
  const requestView = document.getElementById("request-view");
  const responseView = document.getElementById("response-view");
  const errorsBox = document.getElementById("form-errors");
  const pingBtn = document.getElementById("ping-btn");

  function csrfToken() {
    const item = document.cookie.split(";").map(v => v.trim()).find(v => v.startsWith("csrftoken="));
    return item ? decodeURIComponent(item.split("=")[1]) : "";
  }

  function showErrors(errors) {
    const entries = Object.entries(errors || {});
    if (!entries.length) {
      errorsBox.classList.add("d-none");
      errorsBox.textContent = "";
      return;
    }
    errorsBox.innerHTML = entries.map(([k, v]) => `<div><strong>${k}:</strong> ${Array.isArray(v) ? v.join(", ") : v}</div>`).join("");
    errorsBox.classList.remove("d-none");
  }

  function pretty(el, value) {
    el.textContent = JSON.stringify(value, null, 2);
  }

  function toPayload() {
    const command = commandSelect.value;
    const meta = catalog[command] || { params: [] };
    const payload = {
      ip: form.ip.value.trim(),
      port: Number(form.port.value),
      source_node: Number(form.source_node.value),
      dest_node: form.dest_node.value === "" ? null : Number(form.dest_node.value),
      command,
      params: {},
    };

    meta.params.forEach((param) => {
      const field = form.elements[`param_${param.name}`];
      payload.params[param.name] = field ? field.value : null;
    });

    return payload;
  }

  function validate(payload) {
    const errors = {};
    if (!payload.ip) errors.ip = "IP obligatoria.";
    if (!Number.isInteger(payload.port)) errors.port = "Puerto numérico obligatorio.";
    if (!Number.isInteger(payload.source_node)) errors.source_node = "source_node numérico obligatorio.";

    const meta = catalog[payload.command] || {};
    if (meta.requires_dest_node && !Number.isInteger(payload.dest_node)) {
      errors.dest_node = "dest_node es obligatorio para este comando.";
    }

    (meta.params || []).forEach((param) => {
      const value = payload.params[param.name];
      if (param.required && (value === "" || value === null || value === undefined)) {
        errors[param.name] = "Campo obligatorio.";
      }
      if (param.type === "number" && value !== "" && !Number.isFinite(Number(value))) {
        errors[param.name] = "Debe ser numérico.";
      }
      if (param.name === "record_index" && value !== "" && !Number.isInteger(Number(value))) {
        errors[param.name] = "record_index debe ser numérico.";
      }
    });

    return errors;
  }

  function renderParams(command) {
    const meta = catalog[command] || { params: [] };
    paramsContainer.innerHTML = "";
    meta.params.forEach((param) => {
      const col = document.createElement("div");
      col.className = "col-md-4";
      const id = `param_${param.name}`;
      col.innerHTML = `
        <label class="form-label" for="${id}">${param.name}${param.required ? " *" : ""}</label>
        <input class="form-control" id="${id}" name="${id}" type="${param.type || "text"}" ${param.required ? "required" : ""} value="${param.default ?? ""}">
      `;
      paramsContainer.appendChild(col);
    });
  }

  async function postJSON(url, payload) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({ detail: "Respuesta no JSON." }));
    if (!response.ok) {
      const err = new Error(data.detail || `HTTP ${response.status}`);
      err.payload = data;
      throw err;
    }
    return data;
  }

  Object.entries(catalog).forEach(([name, meta]) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = `${name} — ${meta.label || name}`;
    commandSelect.appendChild(option);
  });

  commandSelect.addEventListener("change", () => renderParams(commandSelect.value));
  renderParams(commandSelect.value || commandSelect.options[0]?.value);

  pingBtn.addEventListener("click", async () => {
    const payload = {
      ip: form.ip.value.trim(),
      port: Number(form.port.value),
      source_node: Number(form.source_node.value),
    };
    pretty(requestView, { endpoint: "/api/api3000/ping/", payload });
    showErrors({});
    try {
      const data = await postJSON("/api/api3000/ping/", payload);
      pretty(responseView, data);
    } catch (error) {
      pretty(responseView, error.payload || { detail: error.message });
      showErrors(error.payload?.errors || { error: error.message });
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = toPayload();
    const errors = validate(payload);
    pretty(requestView, { endpoint: "/api/api3000/execute/", payload });
    if (Object.keys(errors).length) {
      showErrors(errors);
      pretty(responseView, { ok: false, errors });
      return;
    }

    showErrors({});
    try {
      const data = await postJSON("/api/api3000/execute/", payload);
      pretty(responseView, data);
    } catch (error) {
      pretty(responseView, error.payload || { detail: error.message });
      showErrors(error.payload?.errors || { error: error.message });
    }
  });
})();
