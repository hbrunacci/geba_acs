(function () {
  const body = document.body;
  if (!body) {
    return;
  }

  function toggleModal(modalId, show) {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    if (show) {
      modal.removeAttribute("hidden");
      modal.querySelector("[data-close-modal]")?.focus();
    } else {
      modal.setAttribute("hidden", "hidden");
    }
  }

  body.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const openTrigger = target.closest("[data-open-modal]");
    if (openTrigger) {
      const modalId = openTrigger.getAttribute("data-open-modal");
      if (modalId) {
        toggleModal(modalId, true);
        event.preventDefault();
      }
      return;
    }

    const closeTrigger = target.closest("[data-close-modal]");
    if (closeTrigger) {
      const modal = closeTrigger.closest(".modal");
      if (modal?.id) {
        toggleModal(modal.id, false);
      }
      return;
    }

    const overlay = target.classList.contains("modal__overlay");
    if (overlay) {
      const modal = target.closest(".modal");
      if (modal?.id) {
        toggleModal(modal.id, false);
      }
    }
  });

  body.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) {
      return;
    }

    const table = target.closest("[data-component='selectable-table']");
    if (!table) {
      return;
    }

    if (target.hasAttribute("data-table-check")) {
      const checkboxes = table.querySelectorAll("tbody input[type='checkbox']");
      checkboxes.forEach((checkbox) => {
        checkbox.checked = target.checked;
        checkbox.dispatchEvent(new Event("change", { bubbles: true }));
      });
      return;
    }

    if (target.type === "checkbox") {
      const headerCheckbox = table.querySelector("thead [data-table-check='all']");
      if (headerCheckbox instanceof HTMLInputElement) {
        const bodyCheckboxes = table.querySelectorAll("tbody input[type='checkbox']");
        const total = bodyCheckboxes.length;
        const checked = table.querySelectorAll("tbody input[type='checkbox']:checked").length;
        headerCheckbox.indeterminate = checked > 0 && checked < total;
        headerCheckbox.checked = checked === total;
      }

      updateBulkActions(table, target);
    }
  });

  function updateBulkActions(table, checkbox) {
    const card = table.closest(".card");
    if (!card) return;
    const actions = card.querySelectorAll("[data-action^='bulk-']");
    const checked = table.querySelectorAll("tbody input[type='checkbox']:checked").length;
    actions.forEach((action) => {
      if (action instanceof HTMLButtonElement) {
        action.disabled = checked === 0;
      }
    });
    const summary = card.querySelector(".table__results");
    if (summary) {
      summary.textContent = checked
        ? `${checked} registro${checked === 1 ? "" : "s"} seleccionado${checked === 1 ? "" : "s"}`
        : "Sin registros seleccionados";
    }
  }
})();
