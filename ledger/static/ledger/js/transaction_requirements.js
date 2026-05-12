(function () {
  const form = document.getElementById("transaction-form");
  if (!form) return;

  const urlTemplate = form.dataset.requirementsUrlTemplate; // ends with /0/requirements/
  const subSel = document.getElementById("id_subcategory");
  const transportSel = document.getElementById("id_transport_type");
  const vehicleSel = document.getElementById("id_vehicle");
  const contactSel = document.getElementById("id_contact");
  const teamSel = document.getElementById("id_team");
  const jobSel = document.getElementById("id_job");
  const assetSel = document.getElementById("id_asset");
  const receiptInput = document.getElementById("id_receipt");
  const invoiceInput = document.getElementById("id_invoice_number");
  const clearInvoiceBtn = document.getElementById("clearInvoiceBtn");

  const badge = document.getElementById("requirementsBadge");
  const list = document.getElementById("requirementsList");
  const hintsEl = document.getElementById("requirementsHints");

  const transportWrap = document.getElementById("transportWrap");
  const vehicleWrap = document.getElementById("vehicleWrap");
  const contactWrap = document.getElementById("contactWrap");

  // Accordion fields
  const teamWrap = document.getElementById("teamWrap");
  const jobWrap = document.getElementById("jobWrap");
  const receiptWrap = document.getElementById("receiptWrap");
  const assetWrap = document.getElementById("assetWrap");

  const transportReq = document.getElementById("transportReq");
  const vehicleReq = document.getElementById("vehicleReq");
  const contactReq = document.getElementById("contactReq");
  const teamReq = document.getElementById("teamReq");
  const jobReq = document.getElementById("jobReq");
  const receiptReq = document.getElementById("receiptReq");
  const assetReq = document.getElementById("assetReq");
  const invoiceReq = document.getElementById("invoiceReq");

  const optCollapseEl = document.getElementById("optCollapse");

  let currentRules = null;

  function buildUrl(pk) {
    return urlTemplate.replace("/0/", `/${pk}/`);
  }

  function setBadge(text, tone) {
    if (!badge) return;
    badge.textContent = text;
    badge.className = "badge";
    if (tone === "ok") badge.classList.add("bg-success");
    else if (tone === "warn") badge.classList.add("bg-warning", "text-dark");
    else badge.classList.add("bg-secondary");
  }

  function liFor(key) {
    return list ? list.querySelector(`li[data-key="${key}"]`) : null;
  }

  function markRequired(key, required) {
    const li = liFor(key);
    if (!li) return;
    li.style.display = required ? "" : "none";
  }

  function isFilled(el) {
    if (!el) return false;
    if (el.type === "file") return !!(el.files && el.files.length);
    const v = (el.value || "").trim();
    return !!v;
  }

  function escapeHtml(str) {
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function renderHints(hints) {
    if (!hintsEl) return;
    if (!hints || !hints.length) {
      hintsEl.textContent = "";
      return;
    }
    hintsEl.innerHTML = hints.map(h => `<div>• ${escapeHtml(h)}</div>`).join("");
  }

  function openOptionalAccordionIfNeeded(requires) {
    if (!optCollapseEl) return;

    const needsAccordion = !!(requires.team || requires.job || requires.receipt || requires.asset);
    const hasErrors = optCollapseEl.querySelector(".is-invalid, .invalid-feedback.d-block");

    if (!needsAccordion && !hasErrors) return;

    // Bootstrap Collapse (no-jQuery)
    try {
      // eslint-disable-next-line no-undef
      const bs = bootstrap.Collapse.getOrCreateInstance(optCollapseEl, { toggle: false });
      bs.show();
    } catch (e) {
      // If Bootstrap isn't available for some reason, do nothing.
    }
  }

  function applyVisibility() {
    if (!currentRules) {
      transportWrap && (transportWrap.style.display = "none");
      vehicleWrap && (vehicleWrap.style.display = "none");
      contactWrap && (contactWrap.style.display = "none");

      teamWrap && (teamWrap.style.display = "none");
      jobWrap && (jobWrap.style.display = "none");
      receiptWrap && (receiptWrap.style.display = "none");
      assetWrap && (assetWrap.style.display = "none");

      return;
    }

    const requires = currentRules.requires || {};
    const vehicleRule = currentRules.vehicle_rule || "none";

    // Contact
    if (contactWrap) contactWrap.style.display = requires.contact ? "" : "none";
    if (contactReq) contactReq.classList.toggle("d-none", !requires.contact);

    // Transport
    if (transportWrap) transportWrap.style.display = requires.transport ? "" : "none";
    if (transportReq) transportReq.classList.toggle("d-none", !requires.transport);

    // Team/Job/Receipt/Asset (accordion)
    if (teamWrap) teamWrap.style.display = requires.team ? "" : "none";
    if (teamReq) teamReq.classList.toggle("d-none", !requires.team);

    if (jobWrap) jobWrap.style.display = requires.job ? "" : "none";
    if (jobReq) jobReq.classList.toggle("d-none", !requires.job);

    if (receiptWrap) receiptWrap.style.display = requires.receipt ? "" : "none";
    if (receiptReq) receiptReq.classList.toggle("d-none", !requires.receipt);

    if (assetWrap) assetWrap.style.display = requires.asset ? "" : "none";
    if (assetReq) assetReq.classList.toggle("d-none", !requires.asset);

    // Invoice (badge only)
    if (invoiceReq) invoiceReq.classList.toggle("d-none", !requires.invoice_number);

    // Vehicle
    const t = (transportSel?.value || "").trim();
    let showVehicle = false;
    if (vehicleRule === "business_vehicle") showVehicle = (t === "business_vehicle");
    if (vehicleRule === "always") showVehicle = true;

    if (vehicleWrap) vehicleWrap.style.display = showVehicle ? "" : "none";
    if (vehicleReq) {
      const req = (vehicleRule === "always") || (vehicleRule === "business_vehicle" && t === "business_vehicle");
      vehicleReq.classList.toggle("d-none", !req);
    }

    if (!showVehicle && vehicleSel) vehicleSel.value = "";

    openOptionalAccordionIfNeeded(requires);
  }

  function updateChecklistAndBadge() {
    if (!currentRules) {
      setBadge("Select a Sub-Category", "neutral");
      return;
    }

    const requires = currentRules.requires || {};
    markRequired("description", true);
    markRequired("amount", true);
    markRequired("date", true);

    markRequired("contact", !!requires.contact);
    markRequired("team", !!requires.team);
    markRequired("job", !!requires.job);
    markRequired("invoice_number", !!requires.invoice_number);
    markRequired("receipt", !!requires.receipt);
    markRequired("asset", !!requires.asset);
    markRequired("transport", !!requires.transport);

    const vehicleRule = currentRules.vehicle_rule || "none";
    const t = (transportSel?.value || "").trim();
    const vehicleReqNow = (vehicleRule === "always") || (vehicleRule === "business_vehicle" && t === "business_vehicle");
    markRequired("vehicle", vehicleReqNow);

    let missing = 0;
    const desc = document.getElementById("id_description");
    if (!isFilled(desc)) missing++;
    const amt = document.getElementById("id_amount");
    if (!isFilled(amt)) missing++;
    const date = document.getElementById("id_date");
    if (!isFilled(date)) missing++;

    if (requires.contact && !isFilled(contactSel)) missing++;
    if (requires.team && !isFilled(teamSel)) missing++;
    if (requires.job && !isFilled(jobSel)) missing++;
    if (requires.invoice_number && !isFilled(invoiceInput)) missing++;
    if (requires.receipt && !isFilled(receiptInput)) missing++;
    if (requires.asset && !isFilled(assetSel)) missing++;
    if (requires.transport && !isFilled(transportSel)) missing++;
    if (vehicleReqNow && !isFilled(vehicleSel)) missing++;

    if (missing === 0) setBadge("All set", "ok");
    else setBadge(`${missing} missing`, "warn");
  }

  async function fetchRules(pk) {
    const url = buildUrl(pk);
    const resp = await fetch(url, { headers: { "Accept": "application/json" } });
    if (!resp.ok) throw new Error(`Failed to load requirements (${resp.status})`);
    return await resp.json();
  }

  async function onSubcategoryChange() {
    const pk = parseInt(subSel?.value || "0", 10);
    if (!pk) {
      currentRules = null;
      applyVisibility();
      renderHints([]);
      setBadge("Select a Sub-Category", "neutral");
      return;
    }

    try {
      currentRules = await fetchRules(pk);
      applyVisibility();
      renderHints(currentRules.hints || []);
      updateChecklistAndBadge();
    } catch (e) {
      currentRules = null;
      applyVisibility();
      renderHints(["Could not load required fields."]);
      setBadge("Rules unavailable", "warn");
    }
  }

  function onTransportChange() {
    if (!currentRules) return;
    applyVisibility();
    updateChecklistAndBadge();
  }

  // Clear invoice button (safe even if invoice isn't required)
  if (clearInvoiceBtn && invoiceInput) {
    clearInvoiceBtn.addEventListener("click", () => {
      invoiceInput.value = "";
      updateChecklistAndBadge();
    });
  }

  // Wire events
  subSel && subSel.addEventListener("change", onSubcategoryChange);
  transportSel && transportSel.addEventListener("change", onTransportChange);

  // Update badge as user fills fields
  ["input", "change"].forEach(evt => {
    form.addEventListener(evt, () => updateChecklistAndBadge(), true);
  });

  // Initial pass (edit form or if subcategory pre-selected)
  onSubcategoryChange();

  // If server-side errors are in the accordion, open it on load
  if (optCollapseEl && optCollapseEl.querySelector(".is-invalid, .invalid-feedback.d-block")) {
    try {
      // eslint-disable-next-line no-undef
      const bs = bootstrap.Collapse.getOrCreateInstance(optCollapseEl, { toggle: false });
      bs.show();
    } catch (e) {}
  }
})();
