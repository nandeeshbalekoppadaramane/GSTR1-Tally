// TaxFlow Pro — GST Accounting Suite - Client Management & Accounting Frontend
let clientsList = [];
let activeClient = null;
let allPartiesData = [];
let allInvoicesData = [];
let currentPage = 1;
const pageSize = 50;

const STATE_CODES = {
  "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
  "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
  "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
  "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
  "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
  "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
  "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra", "29": "Karnataka",
  "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
  "34": "Puducherry", "35": "Andaman & Nicobar Islands", "36": "Telangana",
  "37": "Andhra Pradesh", "38": "Ladakh"
};

const STORAGE_CLIENT_KEY = "agy_active_client_id";
const STORAGE_TAB_KEY = "agy_active_tab_id";
const STORAGE_INVOICES_PAGE = "agy_invoices_page";

document.addEventListener("DOMContentLoaded", () => {
  initApp();
});

function setupTabListeners() {
  document.querySelectorAll('button[data-bs-toggle="tab"]').forEach(tabBtn => {
    tabBtn.addEventListener('shown.bs.tab', (event) => {
      localStorage.setItem(STORAGE_TAB_KEY, event.target.id);
    });
  });
}

function switchToTab(tabId) {
  const trigger = document.getElementById(tabId);
  if (trigger) {
    bootstrap.Tab.getOrCreateInstance(trigger).show();
    localStorage.setItem(STORAGE_TAB_KEY, tabId);
  }
}

async function initApp() {
  setupTabListeners();
  await fetchClients();
  await checkTallyStatus();

  const savedClientId = localStorage.getItem(STORAGE_CLIENT_KEY);
  const savedTabId = localStorage.getItem(STORAGE_TAB_KEY);

  let restoredClient = null;
  if (savedClientId) {
    restoredClient = clientsList.find(c => String(c.id) === String(savedClientId));
  }

  if (restoredClient) {
    await selectClient(restoredClient.id, savedTabId || "overview-tab", false);
  } else {
    activeClient = null;
    localStorage.removeItem(STORAGE_CLIENT_KEY);
    fetch("/api/clients/deselect", { method: "POST" }).catch(() => {});

    updateActiveClientUI(null);
    updateClientViewStates(null);
    renderNavClientsDropdown(clientsList);
    renderClientsGrid(clientsList);

    switchToTab("clients-tab");
  }
}

// Utility: Format currency in Indian Numbering System
function formatINR(val) {
  if (val === undefined || val === null) return "₹0.00";
  return "₹" + Number(val).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

function formatCr(val) {
  if (!val) return "0.00 Cr";
  const cr = Number(val) / 10000000;
  return cr.toFixed(2) + " Cr";
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text).then(() => {
    showToast(`Copied ${text} to clipboard!`);
  }).catch(() => {
    const input = document.createElement("input");
    input.value = text;
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    document.body.removeChild(input);
    showToast(`Copied ${text} to clipboard!`);
  });
}

// ---------------------------------------------------------------------------
// 1. Client Management Database APIs & UI
// ---------------------------------------------------------------------------
function updateDashboardSystemStats() {
  const totalClients = clientsList.length;
  const activeCount = clientsList.filter(c => (c.status || 'active').toLowerCase() === 'active').length;
  const totalPeriods = clientsList.reduce((sum, c) => sum + (Number(c.total_periods) || 0), 0);
  const totalDocs = clientsList.reduce((sum, c) => sum + (Number(c.total_docs) || 0), 0);
  const totalTurnover = clientsList.reduce((sum, c) => sum + (Number(c.total_taxable) || 0), 0);

  const dashClients = document.getElementById("dashTotalClients");
  if (dashClients) dashClients.textContent = totalClients.toLocaleString();

  const dashActive = document.getElementById("dashActiveClientsText");
  if (dashActive) dashActive.textContent = `${activeCount} Active Taxpayers`;

  const dashPeriods = document.getElementById("dashTotalPeriods");
  if (dashPeriods) dashPeriods.textContent = totalPeriods.toLocaleString();

  const dashDocs = document.getElementById("dashTotalDocs");
  if (dashDocs) dashDocs.textContent = totalDocs.toLocaleString();

  const dashTurnover = document.getElementById("dashTotalTurnover");
  if (dashTurnover) dashTurnover.textContent = formatINR(totalTurnover);
}

function updateClientViewStates(client) {
  const kpiMetricsStrip = document.getElementById("kpiMetricsStrip");
  const heroBanner = document.getElementById("activeClientHeroBanner");

  const overviewEmpty = document.getElementById("overviewEmptyState");
  const overviewContent = document.getElementById("overviewContent");

  const partiesEmpty = document.getElementById("partiesEmptyState");
  const partiesContent = document.getElementById("partiesContent");

  const invoicesEmpty = document.getElementById("invoicesEmptyState");
  const invoicesContent = document.getElementById("invoicesContent");

  const tallyEmpty = document.getElementById("tallyEmptyState");
  const tallyContent = document.getElementById("tallyContent");

  if (client) {
    if (kpiMetricsStrip) kpiMetricsStrip.classList.remove("d-none");
    if (heroBanner) heroBanner.classList.remove("d-none");

    if (overviewEmpty) overviewEmpty.classList.add("d-none");
    if (overviewContent) overviewContent.classList.remove("d-none");

    if (partiesEmpty) partiesEmpty.classList.add("d-none");
    if (partiesContent) partiesContent.classList.remove("d-none");

    if (invoicesEmpty) invoicesEmpty.classList.add("d-none");
    if (invoicesContent) invoicesContent.classList.remove("d-none");

    if (tallyEmpty) tallyEmpty.classList.add("d-none");
    if (tallyContent) tallyContent.classList.remove("d-none");
  } else {
    if (kpiMetricsStrip) kpiMetricsStrip.classList.add("d-none");
    if (heroBanner) heroBanner.classList.add("d-none");

    if (overviewEmpty) overviewEmpty.classList.remove("d-none");
    if (overviewContent) overviewContent.classList.add("d-none");

    if (partiesEmpty) partiesEmpty.classList.remove("d-none");
    if (partiesContent) partiesContent.classList.add("d-none");

    if (invoicesEmpty) invoicesEmpty.classList.remove("d-none");
    if (invoicesContent) invoicesContent.classList.add("d-none");

    if (tallyEmpty) tallyEmpty.classList.remove("d-none");
    if (tallyContent) tallyContent.classList.add("d-none");
  }
}

function clearOverviewUI() {
  const kpiDocs = document.getElementById("kpiTotalDocs");
  if (kpiDocs) kpiDocs.textContent = "0";
  const kpiSplit = document.getElementById("kpiDocSplit");
  if (kpiSplit) kpiSplit.textContent = "B2B: 0 | B2C: 0 | CDNR: 0";
  const kpiTaxable = document.getElementById("kpiTaxable");
  if (kpiTaxable) kpiTaxable.textContent = "₹0.00";
  const kpiTaxableCr = document.getElementById("kpiTaxableCr");
  if (kpiTaxableCr) kpiTaxableCr.textContent = "0.00 Cr";
  const kpiTax = document.getElementById("kpiTotalTax");
  if (kpiTax) kpiTax.textContent = "₹0.00";
  const kpiTaxSplit = document.getElementById("kpiTaxSplit");
  if (kpiTaxSplit) kpiTaxSplit.textContent = "CGST: ₹0 | SGST: ₹0";
  const kpiGross = document.getElementById("kpiGross");
  if (kpiGross) kpiGross.textContent = "₹0.00";
  const kpiGrossCr = document.getElementById("kpiGrossCr");
  if (kpiGrossCr) kpiGrossCr.textContent = "0.00 Cr";
  const kpiParties = document.getElementById("kpiParties");
  if (kpiParties) kpiParties.textContent = "0";

  const tbody = document.getElementById("monthlyTableBody");
  if (tbody) tbody.innerHTML = `<tr><td colspan="12" class="text-center py-4 text-muted">No monthly return data found for active client.</td></tr>`;
  const tfoot = document.getElementById("monthlyTableFoot");
  if (tfoot) tfoot.innerHTML = "";
}

async function fetchClients() {
  try {
    const res = await fetch("/api/clients");
    const data = await res.json();
    clientsList = data.clients || [];

    if (activeClient) {
      activeClient = clientsList.find(c => c.id === activeClient.id) || null;
      updateActiveClientUI(activeClient);
      updateClientViewStates(activeClient);
    }

    updateDashboardSystemStats();
    renderNavClientsDropdown(clientsList);
    renderClientsGrid(clientsList);
    return clientsList;
  } catch (err) {
    console.error("Failed to load clients:", err);
    clientsList = [];
    return [];
  }
}

function updateActiveClientUI(client) {
  const heroBanner = document.getElementById("activeClientHeroBanner");
  if (!client) {
    if (heroBanner) heroBanner.classList.add("d-none");

    const companyGstinTitle = document.getElementById("companyGstinTitle");
    if (companyGstinTitle) companyGstinTitle.textContent = "-";
    const companyNameTitle = document.getElementById("companyNameTitle");
    if (companyNameTitle) companyNameTitle.textContent = "No Taxpayer Selected";

    const exportClientName = document.getElementById("exportTargetClientName");
    if (exportClientName) {
      exportClientName.textContent = "No Taxpayer Selected";
      document.getElementById("exportTargetClientGstin").textContent = "-";
      document.getElementById("exportTargetClientTally").textContent = "-";
    }

    const uploadBadge = document.getElementById("uploadClientBadge");
    if (uploadBadge) uploadBadge.textContent = "No Active Client";
    return;
  }

  if (heroBanner) heroBanner.classList.remove("d-none");

  // Tab 1 Hero Banner
  const heroName = document.getElementById("heroClientName");
  if (heroName) {
    heroName.textContent = client.name;
    document.getElementById("heroClientGstin").textContent = client.gstin;
    document.getElementById("heroClientState").textContent = `${client.state_code || ''} - ${client.state_name || ''}`;
    document.getElementById("heroClientPan").textContent = client.pan || client.gstin.substring(2, 12);
    document.getElementById("heroClientTally").textContent = client.tally_company_name || client.name;
  }

  // Tab 2 & 5 Headers
  const companyGstinTitle = document.getElementById("companyGstinTitle");
  if (companyGstinTitle) companyGstinTitle.textContent = client.gstin;
  const companyNameTitle = document.getElementById("companyNameTitle");
  if (companyNameTitle) companyNameTitle.textContent = client.name;

  const exportClientName = document.getElementById("exportTargetClientName");
  if (exportClientName) {
    exportClientName.textContent = client.name;
    document.getElementById("exportTargetClientGstin").textContent = client.gstin;
    document.getElementById("exportTargetClientTally").textContent = client.tally_company_name || client.name;
  }

  const uploadBadge = document.getElementById("uploadClientBadge");
  if (uploadBadge) uploadBadge.textContent = `${client.name} (${client.gstin})`;
}

function renderNavClientsDropdown(list) {
  // No-op: navbar client dropdown has been removed
}

function filterNavClients(query) {
  // No-op: navbar client dropdown has been removed
}

function renderClientsGrid(list) {
  const grid = document.getElementById("clientsGrid");
  if (!grid) return;

  if (list.length === 0) {
    grid.innerHTML = `
      <div class="col-12 text-center py-5">
        <div class="text-muted mb-3"><i class="fa-solid fa-building-circle-exclamation fs-1"></i></div>
        <h6 class="fw-bold">No Taxpayer Clients Found</h6>
        <p class="text-muted small">Create your first client account to begin GSTR-1 returns analysis and Tally export.</p>
        <button class="btn btn-teal btn-sm" onclick="openAddClientModal()"><i class="fa-solid fa-plus me-1"></i> Add New Client</button>
      </div>
    `;
    return;
  }

  grid.innerHTML = list.map(c => {
    const isActive = activeClient && c.id === activeClient.id;
    const periods = c.total_periods || 0;
    const docs = c.total_docs || 0;
    const taxable = c.total_taxable || 0;

    return `
      <div class="col-xl-4 col-md-6">
        <div class="card client-card h-100 shadow-sm ${isActive ? 'is-active-client' : ''}">
          <div class="card-body p-4 d-flex flex-column justify-content-between">
            <div>
              <!-- Top Row: Client Code & Status -->
              <div class="d-flex align-items-center justify-content-between mb-2">
                <div class="d-flex align-items-center gap-2">
                  <span class="badge bg-dark font-mono px-2 py-1">${c.client_code}</span>
                  ${isActive ? '<span class="badge bg-success"><i class="fa-solid fa-circle-check me-1"></i>Active</span>' : ''}
                </div>
                <span class="badge ${c.status === 'active' ? 'bg-success-subtle text-success' : 'bg-secondary-subtle text-secondary'} text-capitalize">
                  ${c.status || 'Active'}
                </span>
              </div>

              <!-- Client Business & Trade Names -->
              <h6 class="fw-bold text-dark mb-1 text-truncate" title="${c.name}">${c.name}</h6>
              ${c.trade_name && c.trade_name !== c.name ? `<div class="small text-muted mb-2 text-truncate" title="Trade Name: ${c.trade_name}"><i class="fa-solid fa-shop me-1 text-teal"></i>${c.trade_name}</div>` : '<div class="mb-2"></div>'}

              <!-- GSTIN Pill with Copy Button -->
              <div class="p-2 bg-light rounded border d-flex align-items-center justify-content-between mb-3 font-mono small">
                <div>
                  <span class="text-muted me-1">GSTIN:</span>
                  <strong class="text-primary">${c.gstin}</strong>
                </div>
                <i class="fa-regular fa-copy copy-gstin-btn text-muted" onclick="copyToClipboard('${c.gstin}')" title="Copy GSTIN"></i>
              </div>

              <!-- Attribute Badges -->
              <div class="d-flex flex-wrap gap-1 mb-3">
                <span class="badge bg-light text-dark border font-mono small">
                  <i class="fa-solid fa-id-badge text-primary me-1"></i>PAN: ${c.pan || c.gstin.substring(2, 12)}
                </span>
                <span class="badge bg-light text-dark border small">
                  <i class="fa-solid fa-location-dot text-danger me-1"></i>${c.state_code || ''} - ${c.state_name || 'India'}
                </span>
                <span class="badge bg-light text-dark border small" title="Tally Company: ${c.tally_company_name || c.name}">
                  <i class="fa-solid fa-building text-info me-1"></i>Tally: ${c.tally_company_name || 'Same as Legal'}
                </span>
              </div>

              <!-- Filing Stats Pill -->
              <div class="row g-2 text-center p-2 rounded bg-light border-0 bg-opacity-50 mb-3" style="background: #f8fafc;">
                <div class="col-4 border-end">
                  <div class="small text-muted" style="font-size: 0.7rem;">PERIODS</div>
                  <div class="fw-bold text-dark">${periods}</div>
                </div>
                <div class="col-4 border-end">
                  <div class="small text-muted" style="font-size: 0.7rem;">DOCS</div>
                  <div class="fw-bold text-primary">${docs.toLocaleString()}</div>
                </div>
                <div class="col-4">
                  <div class="small text-muted" style="font-size: 0.7rem;">TURNOVER</div>
                  <div class="fw-bold text-success">${formatCr(taxable)}</div>
                </div>
              </div>
            </div>

            <!-- Action Buttons Footer -->
            <div class="pt-2 border-top d-flex align-items-center justify-content-between gap-1">
              ${isActive ? `
                <button class="btn btn-sm btn-success disabled py-1 px-2" style="font-size: 0.78rem;">
                  <i class="fa-solid fa-circle-check me-1"></i> Active
                </button>
              ` : `
                <button class="btn btn-sm ${activeClient ? 'btn-outline-teal' : 'btn-teal'} py-1 px-2 fw-semibold" onclick="selectClient(${c.id})" style="font-size: 0.78rem;">
                  <i class="fa-solid fa-arrow-right-to-bracket me-1"></i> ${activeClient ? 'Switch' : 'Select Taxpayer'}
                </button>
              `}
              
              <div class="d-flex align-items-center gap-1">
                <button class="btn btn-sm btn-outline-secondary py-1 px-2" onclick="openEditClientModal(${c.id})" title="Edit Client Profile">
                  <i class="fa-solid fa-pen-to-square"></i>
                </button>
                <button class="btn btn-sm btn-outline-primary py-1 px-2" onclick="viewClientReturns(${c.id})" title="View Monthly Breakdown">
                  <i class="fa-solid fa-table-list"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger py-1 px-2" onclick="deleteClientPrompt(${c.id})" title="Delete Client (${c.client_code})">
                  <i class="fa-solid fa-trash-can"></i>
                </button>
              </div>
            </div>

          </div>
        </div>
      </div>
    `;
  }).join("");
}

function handleClientSearch(query) {
  const q = (query || "").toLowerCase().trim();
  const status = document.getElementById("clientStatusFilter").value;
  const filtered = clientsList.filter(c => {
    const matchesQ = !q || c.name.toLowerCase().includes(q) ||
      (c.trade_name && c.trade_name.toLowerCase().includes(q)) ||
      c.gstin.toLowerCase().includes(q) ||
      c.client_code.toLowerCase().includes(q) ||
      (c.state_name && c.state_name.toLowerCase().includes(q));
    const matchesStatus = !status || (c.status && c.status.toLowerCase() === status.toLowerCase());
    return matchesQ && matchesStatus;
  });
  renderClientsGrid(filtered);
}

function handleClientFilter() {
  const q = document.getElementById("clientSearchFilter").value;
  handleClientSearch(q);
}

async function selectClient(clientId, targetTab = null, showFeedbackToast = true) {
  try {
    const res = await fetch(`/api/clients/${clientId}/select`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      activeClient = clientsList.find(c => c.id === clientId) || data.active_client;
      localStorage.setItem(STORAGE_CLIENT_KEY, String(clientId));
      if (showFeedbackToast) {
        showToast(`Switched active taxpayer to: ${data.active_client.name}`);
      }
      fetch("/api/gstin/verify-portal/reset", { method: "POST" }).catch(() => {});

      updateActiveClientUI(activeClient);
      updateClientViewStates(activeClient);
      renderNavClientsDropdown(clientsList);
      renderClientsGrid(clientsList);

      const savedInvPage = parseInt(localStorage.getItem(STORAGE_INVOICES_PAGE) || "1", 10);

      await Promise.all([
        fetchOverviewData(),
        fetchParties(),
        fetchInvoices(savedInvPage || 1)
      ]);

      if (targetTab) {
        switchToTab(targetTab);
      }
    } else {
      alert("Error switching client: " + (data.detail || "Unknown error"));
    }
  } catch (err) {
    alert("Network error switching client: " + err.message);
  }
}

async function deselectActiveClient() {
  try {
    const res = await fetch("/api/clients/deselect", { method: "POST" });
    if (res.ok) {
      activeClient = null;
      localStorage.removeItem(STORAGE_CLIENT_KEY);
      localStorage.setItem(STORAGE_TAB_KEY, "clients-tab");
      showToast("Active taxpayer client deselected.");

      updateActiveClientUI(null);
      updateClientViewStates(null);
      renderNavClientsDropdown(clientsList);
      renderClientsGrid(clientsList);

      clearOverviewUI();
      allPartiesData = [];
      allInvoicesData = [];
      renderPartiesTable([]);
      renderInvoicesTable([]);

      switchToTab("clients-tab");
    }
  } catch (err) {
    console.error("Error deselecting client:", err);
  }
}

async function viewClientReturns(clientId) {
  if (!activeClient || activeClient.id !== clientId) {
    await selectClient(clientId, 'overview-tab');
  } else {
    switchToTab('overview-tab');
  }
}

// ---------------------------------------------------------------------------
// 2. Add / Edit Client Modal Functions
// ---------------------------------------------------------------------------
function openAddClientModal() {
  document.getElementById("clientModalTitleText").textContent = "Add New Taxpayer Client";
  document.getElementById("clientFormId").value = "";
  document.getElementById("clientForm").reset();
  document.getElementById("clientFormAlert").classList.add("d-none");
  document.getElementById("gstinFeedback").innerHTML = "Type 15-digit GSTIN to auto-derive PAN and State.";
  document.getElementById("gstinFeedback").className = "form-text small";

  const gstUserEl = document.getElementById("clientFormGstUser");
  if (gstUserEl) gstUserEl.value = "";
  const gstPassEl = document.getElementById("clientFormGstPass");
  if (gstPassEl) gstPassEl.value = "";

  const delBtn = document.getElementById("clientModalDeleteBtn");
  if (delBtn) delBtn.classList.add("d-none");

  const modal = new bootstrap.Modal(document.getElementById("clientModal"));
  modal.show();
}

function editActiveClient() {
  if (activeClient) {
    openEditClientModal(activeClient.id);
  }
}

function openEditClientModal(clientId) {
  const c = clientsList.find(item => item.id === clientId);
  if (!c) return;

  document.getElementById("clientModalTitleText").textContent = `Edit Taxpayer Client (${c.client_code})`;
  document.getElementById("clientFormId").value = c.id;
  document.getElementById("clientFormGstin").value = c.gstin;
  document.getElementById("clientFormCode").value = c.client_code;
  document.getElementById("clientFormName").value = c.name;
  document.getElementById("clientFormTradeName").value = c.trade_name || "";
  document.getElementById("clientFormPan").value = c.pan || "";
  document.getElementById("clientFormState").value = `${c.state_code || ''} - ${c.state_name || ''}`;
  document.getElementById("clientFormTallyName").value = c.tally_company_name || "";
  document.getElementById("clientFormTallyId").value = c.tally_company_id || "";
  document.getElementById("clientFormPref").value = c.default_name_preference || "trade";
  document.getElementById("clientFormStatus").value = c.status || "active";
  document.getElementById("clientFormContact").value = c.contact_person || "";
  document.getElementById("clientFormPhone").value = c.phone || "";
  document.getElementById("clientFormEmail").value = c.email || "";
  document.getElementById("clientFormAddress").value = c.address || "";
  document.getElementById("clientFormNotes").value = c.notes || "";
  const gstUserEl = document.getElementById("clientFormGstUser");
  if (gstUserEl) gstUserEl.value = c.gst_username || "";
  const gstPassEl = document.getElementById("clientFormGstPass");
  if (gstPassEl) gstPassEl.value = c.gst_password || "";
  document.getElementById("clientFormAlert").classList.add("d-none");

  const delBtn = document.getElementById("clientModalDeleteBtn");
  if (delBtn) delBtn.classList.remove("d-none");

  onGstinInput(c.gstin);

  const modal = new bootstrap.Modal(document.getElementById("clientModal"));
  modal.show();
}

function deleteClientFromEditModal() {
  const clientId = parseInt(document.getElementById("clientFormId").value, 10);
  if (!clientId) return;
  const editModalEl = document.getElementById("clientModal");
  const editModal = bootstrap.Modal.getInstance(editModalEl);
  if (editModal) editModal.hide();
  deleteClientPrompt(clientId);
}

function onGstinInput(rawVal) {
  const val = (rawVal || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  document.getElementById("clientFormGstin").value = val;

  const feedback = document.getElementById("gstinFeedback");
  const stateInput = document.getElementById("clientFormState");
  const panInput = document.getElementById("clientFormPan");

  if (val.length >= 2) {
    const code = val.substring(0, 2);
    const stateName = STATE_CODES[code] || "Unknown State";
    stateInput.value = `${code} - ${stateName}`;
  } else {
    stateInput.value = "";
  }

  if (val.length >= 12) {
    const pan = val.substring(2, 12);
    panInput.value = pan;
  }

  const gstinRegex = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$/;
  if (val.length === 15) {
    if (gstinRegex.test(val)) {
      feedback.className = "form-text small text-success fw-semibold";
      feedback.innerHTML = `<i class="fa-solid fa-circle-check me-1"></i> Valid GSTIN format (${STATE_CODES[val.substring(0,2)] || 'India'})`;
    } else {
      feedback.className = "form-text small text-warning";
      feedback.innerHTML = `<i class="fa-solid fa-triangle-exclamation me-1"></i> 15 characters, but non-standard checksum/structure.`;
    }
  } else {
    feedback.className = "form-text small text-muted";
    feedback.innerHTML = `${val.length}/15 characters typed. Type full GSTIN to derive PAN & State.`;
  }
}

async function verifyGstinFromApi() {
  const gstin = document.getElementById("clientFormGstin").value;
  if (!gstin || gstin.length < 2) {
    alert("Please enter a GSTIN to verify.");
    return;
  }
  try {
    const res = await fetch(`/api/clients/lookup/gstin/${gstin}`);
    const data = await res.json();
    if (data.pan) document.getElementById("clientFormPan").value = data.pan;
    if (data.state_name) document.getElementById("clientFormState").value = `${data.state_code} - ${data.state_name}`;
    const feedback = document.getElementById("gstinFeedback");
    if (data.is_valid_format) {
      feedback.className = "form-text small text-success fw-semibold";
      feedback.innerHTML = `<i class="fa-solid fa-circle-check me-1"></i> Verified structure (${data.state_name})`;
    } else {
      feedback.className = "form-text small text-warning";
      feedback.innerHTML = `<i class="fa-solid fa-triangle-exclamation me-1"></i> Non-standard format detected.`;
    }
  } catch (err) {
    console.error(err);
  }
}

async function detectTallyForClientForm() {
  const btn = event.currentTarget;
  const originalText = btn.innerHTML;
  btn.innerHTML = `<div class="spinner-border spinner-border-sm me-1" role="status"></div> Checking...`;
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    if (data.tally && data.tally.online && data.tally.company) {
      document.getElementById("clientFormTallyName").value = data.tally.company;
      showToast(`Detected open company: ${data.tally.company}`);
    } else {
      alert("Could not detect open company in TallyPrime. Ensure Tally is open on port 9000.");
    }
  } catch (err) {
    alert("Tally connection error: " + err.message);
  } finally {
    btn.innerHTML = originalText;
  }
}

async function handleClientSubmit(e) {
  e.preventDefault();
  const alertBox = document.getElementById("clientFormAlert");
  alertBox.classList.add("d-none");

  const clientId = document.getElementById("clientFormId").value;
  const isEdit = Boolean(clientId);

  const payload = {
    name: document.getElementById("clientFormName").value.trim(),
    trade_name: document.getElementById("clientFormTradeName").value.trim() || undefined,
    gstin: document.getElementById("clientFormGstin").value.trim().toUpperCase(),
    client_code: document.getElementById("clientFormCode").value.trim() || undefined,
    pan: document.getElementById("clientFormPan").value.trim().toUpperCase() || undefined,
    tally_company_name: document.getElementById("clientFormTallyName").value.trim() || undefined,
    tally_company_id: document.getElementById("clientFormTallyId").value.trim() || undefined,
    default_name_preference: document.getElementById("clientFormPref").value,
    status: document.getElementById("clientFormStatus").value,
    contact_person: document.getElementById("clientFormContact").value.trim() || undefined,
    phone: document.getElementById("clientFormPhone").value.trim() || undefined,
    email: document.getElementById("clientFormEmail").value.trim() || undefined,
    address: document.getElementById("clientFormAddress").value.trim() || undefined,
    notes: document.getElementById("clientFormNotes").value.trim() || undefined,
    gst_username: document.getElementById("clientFormGstUser") ? (document.getElementById("clientFormGstUser").value.trim() || undefined) : undefined,
    gst_password: document.getElementById("clientFormGstPass") ? (document.getElementById("clientFormGstPass").value.trim() || undefined) : undefined,
  };

  const saveBtn = document.getElementById("clientFormSaveBtn");
  saveBtn.disabled = true;
  saveBtn.innerHTML = `<div class="spinner-border spinner-border-sm me-1" role="status"></div> Saving...`;

  try {
    const url = isEdit ? `/api/clients/${clientId}` : "/api/clients";
    const method = isEdit ? "PUT" : "POST";

    const res = await fetch(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!res.ok) {
      alertBox.textContent = data.detail || "Failed to save client.";
      alertBox.classList.remove("d-none");
      return;
    }

    showToast(isEdit ? `Client ${data.client.client_code} updated!` : `New client ${data.client.client_code} created!`);
    const modalEl = document.getElementById("clientModal");
    const modal = bootstrap.Modal.getInstance(modalEl);
    if (modal) modal.hide();

    await fetchClients();
  } catch (err) {
    alertBox.textContent = "Network error: " + err.message;
    alertBox.classList.remove("d-none");
  } finally {
    saveBtn.disabled = false;
    saveBtn.innerHTML = `<i class="fa-solid fa-floppy-disk me-1"></i> Save Client Profile`;
  }
}

let pendingDeleteClientId = null;

function deleteClientPrompt(clientId) {
  const c = clientsList.find(item => item.id === clientId);
  if (!c) return;

  pendingDeleteClientId = clientId;
  document.getElementById("delClientName").textContent = c.name;
  document.getElementById("delClientGstin").textContent = `GSTIN: ${c.gstin} (${c.client_code})`;

  const activeWarn = document.getElementById("delClientActiveWarning");
  if (activeWarn) {
    if (activeClient && activeClient.id === clientId) {
      activeWarn.classList.remove("d-none");
    } else {
      activeWarn.classList.add("d-none");
    }
  }

  const filesCheckbox = document.getElementById("delClientFilesCheckbox");
  if (filesCheckbox) filesCheckbox.checked = true;

  const modal = new bootstrap.Modal(document.getElementById("deleteClientModal"));
  modal.show();
}

async function executeClientDelete() {
  if (!pendingDeleteClientId) return;
  const btn = document.getElementById("confirmDelClientBtn");
  btn.disabled = true;
  btn.innerHTML = `<div class="spinner-border spinner-border-sm me-1" role="status"></div> Deleting...`;

  const deleteFiles = document.getElementById("delClientFilesCheckbox") ? document.getElementById("delClientFilesCheckbox").checked : true;

  try {
    const res = await fetch(`/api/clients/${pendingDeleteClientId}?delete_files=${deleteFiles}`, { method: "DELETE" });
    const data = await res.json();
    if (!res.ok) {
      alert(data.detail || "Delete failed.");
      return;
    }
    showToast(data.message || "Client account deleted successfully.");
    const modalEl = document.getElementById("deleteClientModal");
    const modal = bootstrap.Modal.getInstance(modalEl);
    if (modal) modal.hide();

    const wasActive = activeClient && activeClient.id === pendingDeleteClientId;
    if (wasActive) {
      activeClient = null;
      localStorage.removeItem(STORAGE_CLIENT_KEY);
      localStorage.setItem(STORAGE_TAB_KEY, "clients-tab");
      updateActiveClientUI(null);
      updateClientViewStates(null);
      clearOverviewUI();
      allPartiesData = [];
      allInvoicesData = [];
      renderPartiesTable([]);
      renderInvoicesTable([]);
      switchToTab('clients-tab');
    }

    await fetchClients();
    if (activeClient) {
      await fetchOverviewData();
      await fetchParties();
      await fetchInvoices(1);
    }
  } catch (err) {
    alert("Error deleting client: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<i class="fa-solid fa-trash-can me-1"></i> Yes, Delete Client`;
    pendingDeleteClientId = null;
  }
}

// ---------------------------------------------------------------------------
// 3. TallyPrime Status Check
// ---------------------------------------------------------------------------
async function checkTallyStatus() {
  const pill = document.getElementById("tallyStatusPill");
  const text = document.getElementById("tallyStatusText");
  const liveBadge = document.getElementById("tallyLiveBadge");
  const endpoint = document.getElementById("tallyEndpointInput") ? document.getElementById("tallyEndpointInput").value : "http://localhost:9000";

  pill.className = "status-pill status-checking";
  text.textContent = "Connecting to Tally...";

  try {
    const res = await fetch(`/api/status?endpoint=${encodeURIComponent(endpoint)}`);
    const data = await res.json();
    if (data.tally && data.tally.online) {
      pill.className = "status-pill status-online";
      text.textContent = `Tally: ${data.tally.company}`;
      if (liveBadge) {
        liveBadge.className = "badge bg-success";
        liveBadge.textContent = "Port 9000 Connected (" + data.tally.company + ")";
      }
    } else {
      pill.className = "status-pill status-offline";
      text.textContent = "Tally Offline (Port 9000)";
      if (liveBadge) {
        liveBadge.className = "badge bg-warning text-dark";
        liveBadge.textContent = "Tally Offline";
      }
    }
  } catch (err) {
    pill.className = "status-pill status-offline";
    text.textContent = "Tally Offline";
    if (liveBadge) {
      liveBadge.className = "badge bg-warning text-dark";
      liveBadge.textContent = "Tally Offline";
    }
  }
}

// ---------------------------------------------------------------------------
// 4. Fetch Overview & Monthly Stats
// ---------------------------------------------------------------------------
async function fetchOverviewData() {
  try {
    const res = await fetch("/api/overview");
    const data = await res.json();
    const totals = data.totals || {};
    const months = data.monthly_stats || [];

    // Update KPIs
    document.getElementById("kpiTotalDocs").textContent = (totals.total_documents || 0).toLocaleString("en-IN");
    document.getElementById("kpiDocSplit").textContent = 
      `B2B: ${(totals.total_b2b || 0).toLocaleString()} | B2C: ${(totals.total_b2c || 0).toLocaleString()} | CDNR: ${(totals.total_cdnr || 0).toLocaleString()}`;
    
    document.getElementById("kpiTaxable").textContent = formatINR(totals.taxable_turnover);
    document.getElementById("kpiTaxableCr").textContent = formatCr(totals.taxable_turnover);

    document.getElementById("kpiTotalTax").textContent = formatINR(totals.total_tax);
    document.getElementById("kpiTaxSplit").textContent = 
      `CGST: ${formatINR(totals.total_cgst)} | SGST: ${formatINR(totals.total_sgst)}`;

    document.getElementById("kpiGross").textContent = formatINR(totals.gross_invoiced_value);
    document.getElementById("kpiGrossCr").textContent = formatCr(totals.gross_invoiced_value);

    document.getElementById("kpiParties").textContent = totals.unique_parties || 0;

    // Update Preference Indicator
    updatePrefUI(data.name_preference);

    // Populate Monthly Table
    renderMonthlyTable(months, totals);

  } catch (err) {
    console.error("Failed to load overview data:", err);
  }
}

function renderMonthlyTable(months, totals) {
  const tbody = document.getElementById("monthlyTableBody");
  const tfoot = document.getElementById("monthlyTableFoot");

  if (!months || months.length === 0) {
    tbody.innerHTML = `<tr><td colspan="12" class="text-center py-4 text-muted">No monthly return data found for active client. Use 'Upload GSTR-1' to import return files.</td></tr>`;
    tfoot.innerHTML = "";
    return;
  }

  tbody.innerHTML = months.map(m => `
    <tr>
      <td><strong class="text-primary">${m.label}</strong></td>
      <td class="text-center fw-semibold">${m.total_docs.toLocaleString()}</td>
      <td class="text-center text-muted">${m.b2b_count.toLocaleString()}</td>
      <td class="text-center text-muted">${m.b2c_count.toLocaleString()}</td>
      <td class="text-center text-muted">${m.cdnr_count.toLocaleString()}</td>
      <td class="text-center badge-party">${m.parties_count}</td>
      <td class="text-end font-mono">${formatINR(m.taxable_val)}</td>
      <td class="text-end font-mono text-muted">${formatINR(m.cgst_val)}</td>
      <td class="text-end font-mono text-muted">${formatINR(m.sgst_val)}</td>
      <td class="text-end font-mono text-muted">${formatINR(m.igst_val)}</td>
      <td class="text-end font-mono fw-bold text-dark">${formatINR(m.total_val)}</td>
      <td class="text-center">
        <button class="btn btn-outline-danger btn-sm py-0 px-2" style="font-size: 0.72rem;" onclick="deleteReturnPeriod('${m.label}')" title="Delete ${m.label} data">
          <i class="fa-solid fa-trash-can"></i>
        </button>
      </td>
    </tr>
  `).join("");

  tfoot.innerHTML = `
    <tr>
      <th>Total (${months.length} Months)</th>
      <th class="text-center">${(totals.total_documents || 0).toLocaleString()}</th>
      <th class="text-center">${(totals.total_b2b || 0).toLocaleString()}</th>
      <th class="text-center">${(totals.total_b2c || 0).toLocaleString()}</th>
      <th class="text-center">${(totals.total_cdnr || 0).toLocaleString()}</th>
      <th class="text-center">${totals.unique_parties || 0}</th>
      <th class="text-end font-mono">${formatINR(totals.taxable_turnover)}</th>
      <th class="text-end font-mono">${formatINR(totals.total_cgst)}</th>
      <th class="text-end font-mono">${formatINR(totals.total_sgst)}</th>
      <th class="text-end font-mono">${formatINR(totals.total_igst)}</th>
      <th class="text-end font-mono text-warning">${formatINR(totals.gross_invoiced_value)}</th>
      <th class="text-center">-</th>
    </tr>
  `;
}

async function deleteReturnPeriod(label) {
  if (!activeClient) return;
  if (!confirm(`Are you sure you want to delete return data for period '${label}'?`)) return;
  try {
    const res = await fetch(`/api/clients/${activeClient.id}/periods/${encodeURIComponent(label)}`, { method: "DELETE" });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || `Period ${label} deleted.`);
      await fetchOverviewData();
      await fetchParties();
      await fetchInvoices(1);
    } else {
      alert("Failed to delete period: " + (data.detail || "Unknown error"));
    }
  } catch (err) {
    alert("Network error: " + err.message);
  }
}

async function clearClientReturnsPrompt() {
  if (!activeClient) return;
  if (!confirm(`Are you sure you want to clear ALL uploaded return files for client '${activeClient.name}'? This cannot be undone.`)) return;
  try {
    const res = await fetch(`/api/clients/${activeClient.id}/clear-returns`, { method: "DELETE" });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message || "All return files cleared.");
      await fetchOverviewData();
      await fetchParties();
      await fetchInvoices(1);
    } else {
      alert("Failed to clear returns: " + (data.detail || "Unknown error"));
    }
  } catch (err) {
    alert("Network error: " + err.message);
  }
}

// ---------------------------------------------------------------------------
// 5. Counterparties Table & Ledger Formatting
// ---------------------------------------------------------------------------
async function fetchParties() {
  try {
    const res = await fetch("/api/parties");
    const data = await res.json();
    allPartiesData = data.parties || [];

    document.getElementById("partyCountBadge").textContent = `${allPartiesData.length} Parties`;
    renderPartiesTable(allPartiesData);
  } catch (err) {
    console.error("Failed to load counterparties:", err);
  }
}

function renderPartiesTable(parties) {
  const tbody = document.getElementById("partiesTableBody");
  if (!parties || parties.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4 text-muted">No counterparties found.</td></tr>`;
    return;
  }

  tbody.innerHTML = parties.map((p, idx) => {
    const isVerified = Boolean(p.is_verified);
    const hasCustom = Boolean(p.has_custom_mapping);
    const jurTitle = [p.center_jurisdiction, p.state_jurisdiction].filter(Boolean).join(" • ");

    return `
      <tr>
        <td class="text-muted small">${idx + 1}</td>
        <td>
          <div class="d-flex align-items-center gap-1">
            <span class="font-mono fw-semibold text-primary">${p.gstin}</span>
            <i class="fa-regular fa-copy copy-gstin-btn text-muted" onclick="copyToClipboard('${p.gstin}')" title="Copy GSTIN" style="cursor: pointer; font-size: 0.75rem;"></i>
          </div>
        </td>
        <td>
          <strong class="${p.trade_name === 'NA' ? 'text-muted fst-italic' : 'text-dark'}">${p.trade_name}</strong>
        </td>
        <td class="text-muted small">${p.legal_name}</td>
        <td>
          <span class="badge bg-light text-dark border">${p.state_code} - ${p.state_name}</span>
        </td>
        <td>
          <span class="font-mono text-dark fw-semibold">${p.ledger_name}</span>
        </td>
        <td class="text-center">
          ${isVerified ? `
            <span class="badge bg-success-subtle text-success border border-success border-opacity-25" title="${jurTitle || 'Verified from GST Portal'}">
              <i class="fa-solid fa-circle-check me-1"></i>Verified
            </span>
          ` : `
            <span class="badge bg-warning-subtle text-warning border border-warning border-opacity-25">
              <i class="fa-solid fa-clock me-1"></i>Unverified
            </span>
          `}
        </td>
        <td class="text-center text-nowrap">
          ${hasCustom ? `
            <button class="btn btn-sm btn-outline-danger py-0 px-2" onclick="deletePartyMappingPrompt('${p.gstin}', '${(p.trade_name || '').replace(/'/g, "\\'")}')" title="Delete Saved Mapping">
              <i class="fa-solid fa-trash-can me-1"></i>Delete
            </button>
          ` : '<span class="text-muted small">-</span>'}
        </td>
      </tr>
    `;
  }).join("");
}

function filterParties() {
  const query = document.getElementById("partySearchInput").value.toLowerCase().trim();
  if (!query) {
    renderPartiesTable(allPartiesData);
    return;
  }

  const filtered = allPartiesData.filter(p => 
    p.gstin.toLowerCase().includes(query) ||
    p.trade_name.toLowerCase().includes(query) ||
    p.legal_name.toLowerCase().includes(query) ||
    p.state_name.toLowerCase().includes(query) ||
    p.ledger_name.toLowerCase().includes(query)
  );
  renderPartiesTable(filtered);
}

function exportPartyMappings() {
  window.open("/api/export-parties-csv", "_blank");
}

// ---------------------------------------------------------------------------
// 6. Normalized Transactions Explorer
// ---------------------------------------------------------------------------
async function fetchInvoices(page = 1) {
  currentPage = page;
  localStorage.setItem(STORAGE_INVOICES_PAGE, String(page));
  const search = document.getElementById("invSearchInput") ? document.getElementById("invSearchInput").value : "";
  const docType = document.getElementById("invTypeFilter") ? document.getElementById("invTypeFilter").value : "";

  const tbody = document.getElementById("invoicesTableBody");
  tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-muted"><div class="spinner-border spinner-border-sm me-2 text-teal" role="status"></div>Loading vouchers...</td></tr>`;

  try {
    const res = await fetch(`/api/invoices?page=${page}&limit=${pageSize}&search=${encodeURIComponent(search)}&doc_type=${encodeURIComponent(docType)}`);
    const data = await res.json();
    const items = data.items || data.invoices || [];
    const total = data.total !== undefined ? data.total : (data.total_records || 0);
    const limit = data.limit || data.page_size || pageSize;

    allInvoicesData = items;
    document.getElementById("invCountBadge").textContent = `${total.toLocaleString()} Vouchers`;
    renderInvoicesTable(items);
    renderPagination(total, data.page || page, limit);

    const start = total === 0 ? 0 : (page - 1) * limit + 1;
    const end = Math.min(page * limit, total);
    document.getElementById("invPaginationInfo").textContent = 
      total === 0 ? "Showing 0 vouchers" : `Showing ${start} to ${end} of ${total.toLocaleString()} vouchers`;

  } catch (err) {
    console.error("Failed to load invoices:", err);
    tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-danger">Failed to load transactions: ${err.message}</td></tr>`;
  }
}

function renderInvoicesTable(items) {
  const tbody = document.getElementById("invoicesTableBody");
  if (!items || items.length === 0) {
    tbody.innerHTML = `<tr><td colspan="9" class="text-center py-4 text-muted">No transactions found matching your criteria.</td></tr>`;
    return;
  }

  tbody.innerHTML = items.map(inv => {
    let typeBadge = "bg-primary";
    const dtype = (inv.doc_type || "").toUpperCase();
    if (dtype === "B2CS") typeBadge = "bg-info";
    else if (dtype.includes("CREDIT") || dtype.includes("CDNR")) typeBadge = "bg-danger";

    const docNum = inv.doc_num || inv.doc_number || "-";
    const partyName = inv.party_ledger || inv.ledger_name || inv.party_name || "Party";
    const partyGstin = inv.party_gstin || "B2C Retail";
    const rate = inv.tax_rate !== undefined ? inv.tax_rate : 18;
    const tax = inv.total_tax !== undefined ? inv.total_tax : ((inv.cgst || 0) + (inv.sgst || 0) + (inv.igst || 0));

    return `
      <tr>
        <td><span class="badge ${typeBadge}">${inv.doc_type}</span></td>
        <td><strong class="font-mono text-dark">${docNum}</strong></td>
        <td class="font-mono text-muted small">${inv.doc_date || "-"}</td>
        <td>
          <div class="fw-semibold text-truncate" style="max-width: 280px;" title="${partyName}">
            ${partyName}
          </div>
        </td>
        <td><span class="font-mono small text-muted">${partyGstin}</span></td>
        <td class="text-center font-mono small">${rate}%</td>
        <td class="text-end font-mono">${formatINR(inv.taxable_val)}</td>
        <td class="text-end font-mono text-muted">${formatINR(tax)}</td>
        <td class="text-end font-mono fw-bold text-dark">${formatINR(inv.total_val)}</td>
      </tr>
    `;
  }).join("");
}

let invDebounceTimer;
function filterInvoices() {
  clearTimeout(invDebounceTimer);
  invDebounceTimer = setTimeout(() => {
    fetchInvoices(1);
  }, 300);
}

function renderPagination(total, curPage, limit) {
  const container = document.getElementById("invPagination");
  const totalPages = Math.ceil(total / limit);
  if (totalPages <= 1) {
    container.innerHTML = "";
    return;
  }

  let html = "";
  html += `<li class="page-item ${curPage === 1 ? 'disabled' : ''}">
    <a class="page-link" href="#" onclick="fetchInvoices(${curPage - 1}); return false;">&laquo;</a>
  </li>`;

  let start = Math.max(1, curPage - 2);
  let end = Math.min(totalPages, curPage + 2);

  if (start > 1) {
    html += `<li class="page-item"><a class="page-link" href="#" onclick="fetchInvoices(1); return false;">1</a></li>`;
    if (start > 2) html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
  }

  for (let p = start; p <= end; p++) {
    html += `<li class="page-item ${p === curPage ? 'active' : ''}">
      <a class="page-link" href="#" onclick="fetchInvoices(${p}); return false;">${p}</a>
    </li>`;
  }

  if (end < totalPages) {
    if (end < totalPages - 1) html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
    html += `<li class="page-item"><a class="page-link" href="#" onclick="fetchInvoices(${totalPages}); return false;">${totalPages}</a></li>`;
  }

  html += `<li class="page-item ${curPage === totalPages ? 'disabled' : ''}">
    <a class="page-link" href="#" onclick="fetchInvoices(${curPage + 1}); return false;">&raquo;</a>
  </li>`;

  container.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 7. Preference Management
// ---------------------------------------------------------------------------
async function setPreference(pref, event) {
  if (event) event.preventDefault();
  try {
    const res = await fetch(`/api/preferences/name?pref=${pref}`, { method: "POST" });
    const data = await res.json();
    if (data.status === "success") {
      updatePrefUI(pref);
      showToast(`Naming preference set to: ${pref === 'trade' ? 'Trade Name' : 'Legal Name'}`);
      await fetchParties();
      await fetchInvoices(currentPage);
    }
  } catch (err) {
    console.error("Failed to update preference:", err);
  }
}

function updatePrefUI(pref) {
  const isTrade = pref === "trade";
  const label = document.getElementById("activePrefLabel");
  if (label) label.textContent = isTrade ? "Trade Name" : "Legal Name";

  const badge = document.getElementById("namingConventionBadge");
  if (badge) badge.textContent = isTrade ? "Using: Trade Name" : "Using: Legal Name";

  const checkTrade = document.querySelector(".check-trade");
  const checkLegal = document.querySelector(".check-legal");
  if (checkTrade && checkLegal) {
    if (isTrade) {
      checkTrade.classList.remove("d-none");
      checkLegal.classList.add("d-none");
    } else {
      checkTrade.classList.add("d-none");
      checkLegal.classList.remove("d-none");
    }
  }

  const radioTrade = document.getElementById("xmlRadioTrade");
  const radioLegal = document.getElementById("xmlRadioLegal");
  if (radioTrade && radioLegal) {
    if (isTrade) radioTrade.checked = true;
    else radioLegal.checked = true;
  }
}

function handleXmlPrefChange(pref) {
  setPreference(pref);
}

// ---------------------------------------------------------------------------
// 8. XML Generation & Live Tally Import
// ---------------------------------------------------------------------------
async function triggerXmlGeneration() {
  if (!activeClient) {
    alert("Please select an active taxpayer client before generating Consolidated Tally XML.");
    switchToTab("clients-tab");
    return;
  }
  const btn = document.getElementById("btnGenerateXml");
  const container = document.getElementById("xmlFilesContainer");
  const origHtml = btn.innerHTML;

  btn.disabled = true;
  btn.innerHTML = `<div class="spinner-border spinner-border-sm me-2" role="status"></div>Generating Consolidated XML...`;

  try {
    const res = await fetch("/api/generate-xml", { method: "POST" });
    const data = await res.json();
    if (data.status === "success") {
      container.classList.remove("d-none");
      document.getElementById("mastersFileName").textContent = data.masters_file;
      document.getElementById("mastersFileSize").textContent = 
        `${(data.masters_size / 1024).toFixed(1)} KB • ${data.master_ledgers_count} Master Ledgers`;
      
      document.getElementById("entriesFileName").textContent = data.entries_file;
      document.getElementById("entriesFileSize").textContent = 
        `${(data.entries_size / 1024).toFixed(1)} KB • ${data.vouchers_count.toLocaleString()} Vouchers`;

      showToast("Consolidated XML generated successfully!");
    } else {
      alert("Failed to generate XML: " + (data.detail || "Unknown error"));
    }
  } catch (err) {
    alert("Generation error: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = origHtml;
  }
}

async function importToTally(target) {
  if (!activeClient) {
    alert("Please select an active taxpayer client before importing into TallyPrime.");
    switchToTab("clients-tab");
    return;
  }
  const endpoint = document.getElementById("tallyEndpointInput").value;
  const resultBox = document.getElementById("importResultBox");
  const badge = document.getElementById("importResultStatusBadge");
  const logElem = document.getElementById("importDetailsLog");

  resultBox.classList.remove("d-none");
  badge.className = "badge bg-secondary";
  badge.textContent = "Posting to Tally...";
  logElem.textContent = `Connecting to ${endpoint} and importing ${target.toUpperCase()}...`;

  try {
    const res = await fetch("/api/import-tally", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target: target, endpoint: endpoint })
    });

    const data = await res.json();
    if (!res.ok) {
      badge.className = "badge bg-danger";
      badge.textContent = "Failed";
      logElem.textContent = data.detail || "Tally server returned an error.";
      showToast("Tally Import Failed: " + (data.detail || "Server error"));
      return;
    }

    const results = data.results || {};
    let totalCreated = 0;
    let totalAltered = 0;
    let totalErrors = 0;
    let totalExceptions = 0;
    let logLines = [];

    for (const [key, val] of Object.entries(results)) {
      totalCreated += val.created || 0;
      totalAltered += val.altered || 0;
      totalErrors += val.errors || 0;
      totalExceptions += val.exceptions || 0;

      logLines.push(`<strong>${key.toUpperCase()}</strong>: Created=${val.created}, Altered=${val.altered}, Errors=${val.errors}, Exceptions=${val.exceptions}`);
      if (val.line_errors && val.line_errors.length > 0) {
        logLines.push(`<span class="text-danger">Details: ${val.line_errors.join("; ")}</span>`);
      }
    }

    document.getElementById("resCreated").textContent = totalCreated.toLocaleString();
    document.getElementById("resAltered").textContent = totalAltered.toLocaleString();
    document.getElementById("resErrors").textContent = totalErrors.toLocaleString();
    document.getElementById("resExceptions").textContent = totalExceptions.toLocaleString();

    if (totalErrors === 0 && totalExceptions === 0) {
      badge.className = "badge bg-success";
      badge.textContent = "Import Success";
      showToast(`Imported to Tally: ${totalCreated} created, ${totalAltered} altered.`);
    } else {
      badge.className = "badge bg-warning text-dark";
      badge.textContent = "Completed with Issues";
      showToast("Tally import completed with errors/exceptions. Check summary.");
    }

    logElem.innerHTML = logLines.join("<br>");

  } catch (err) {
    badge.className = "badge bg-danger";
    badge.textContent = "Connection Error";
    logElem.textContent = err.message;
    showToast("Network Error: " + err.message);
  }
}

// ---------------------------------------------------------------------------
// 9. File Upload Handler (Client Aware)
// ---------------------------------------------------------------------------
async function uploadGstrFiles() {
  if (!activeClient) {
    alert("Please select an active taxpayer client before uploading GSTR-1 returns.");
    switchToTab("clients-tab");
    return;
  }

  const fileInput = document.getElementById("gstr1FileInput");
  if (!fileInput.files || fileInput.files.length === 0) {
    alert("Please select at least one .json or .zip file.");
    return;
  }

  const formData = new FormData();
  for (const f of fileInput.files) {
    formData.append("files", f);
  }

  const progress = document.getElementById("uploadProgress");
  progress.classList.remove("d-none");

  try {
    const clientId = activeClient.id;
    const clearExisting = document.getElementById("uploadClearExistingCheckbox") ? document.getElementById("uploadClearExistingCheckbox").checked : false;
    const res = await fetch(`/api/upload?client_id=${clientId}&clear_existing=${clearExisting}`, {
      method: "POST",
      body: formData
    });
    const data = await res.json();
    if (data.status === "success") {
      showToast(`Uploaded ${data.uploaded_files.length} file(s) for ${activeClient ? activeClient.name : 'client'}!`);
      const modal = bootstrap.Modal.getInstance(document.getElementById("uploadModal"));
      if (modal) modal.hide();
      fileInput.value = "";
      const clearCheck = document.getElementById("uploadClearExistingCheckbox");
      if (clearCheck) clearCheck.checked = false;
      await fetchClients();
      await fetchOverviewData();
      await fetchParties();
      await fetchInvoices(1);
    } else {
      alert("Upload failed: " + (data.detail || "Server error"));
    }
  } catch (err) {
    alert("Upload error: " + err.message);
  } finally {
    progress.classList.add("d-none");
  }
}

// ---------------------------------------------------------------------------
// Helpers: Tab Switcher & Toast
// ---------------------------------------------------------------------------
function switchToTab(tabId) {
  const trigger = document.getElementById(tabId);
  if (trigger) {
    bootstrap.Tab.getOrCreateInstance(trigger).show();
  }
}

function showToast(msg) {
  const toastEl = document.getElementById("appToast");
  const msgEl = document.getElementById("toastMessage");
  if (toastEl && msgEl) {
    msgEl.textContent = msg;
    const toast = new bootstrap.Toast(toastEl, { delay: 4000 });
    toast.show();
  }
}


// ---------------------------------------------------------------------------
// 9. GST Portal Live Verification & Permanent Storage
// ---------------------------------------------------------------------------
let verificationPollingInterval = null;
let pendingDelPartyGstin = null;

function togglePasswordVisibility(inputId, btn) {
  const el = document.getElementById(inputId);
  if (!el) return;
  const icon = btn.querySelector("i");
  if (el.type === "password") {
    el.type = "text";
    if (icon) {
      icon.classList.remove("fa-eye");
      icon.classList.add("fa-eye-slash");
    }
  } else {
    el.type = "password";
    if (icon) {
      icon.classList.remove("fa-eye-slash");
      icon.classList.add("fa-eye");
    }
  }
}

let currentPortalTab = 'setup';

function switchPortalView(tab) {
  currentPortalTab = tab;
  const setupSec = document.getElementById("portalSetupSection");
  const progSec = document.getElementById("portalProgressSection");
  const tabSetupBtn = document.getElementById("portalTabSetupBtn");
  const tabProgBtn = document.getElementById("portalTabProgressBtn");
  const backFooterBtn = document.getElementById("portalBackToSetupFooterBtn");
  const startBtn = document.getElementById("portalStartBtn");

  if (tab === 'setup') {
    if (setupSec) setupSec.classList.remove("d-none");
    if (progSec) progSec.classList.add("d-none");
    if (tabSetupBtn) tabSetupBtn.classList.add("active");
    if (tabProgBtn) tabProgBtn.classList.remove("active");
    if (backFooterBtn) backFooterBtn.classList.add("d-none");
    if (startBtn) startBtn.classList.remove("d-none");
  } else {
    if (setupSec) setupSec.classList.add("d-none");
    if (progSec) progSec.classList.remove("d-none");
    if (tabSetupBtn) tabSetupBtn.classList.remove("active");
    if (tabProgBtn) tabProgBtn.classList.add("active");
    if (backFooterBtn) backFooterBtn.classList.remove("d-none");
  }
}

async function resetPortalModalToSetup() {
  try {
    await fetch("/api/gstin/verify-portal/reset", { method: "POST" });
  } catch (e) {
    console.warn("Reset error:", e);
  }
  if (verificationPollingInterval) {
    clearInterval(verificationPollingInterval);
    verificationPollingInterval = null;
  }
  const pBar = document.getElementById("portalProgressBar");
  if (pBar) pBar.style.width = "0%";
  const pCounter = document.getElementById("portalProgressCounter");
  if (pCounter) pCounter.textContent = "0 / 0";
  const pLabel = document.getElementById("portalProgressLabel");
  if (pLabel) pLabel.textContent = "Ready to start";
  const statusPill = document.getElementById("portalStatusPill");
  if (statusPill) {
    statusPill.textContent = "Idle - Ready to start";
    statusPill.className = "badge bg-secondary";
  }
  const completeBanner = document.getElementById("portalCompleteBanner");
  if (completeBanner) completeBanner.classList.add("d-none");
  const manualBanner = document.getElementById("portalManualLoginBanner");
  if (manualBanner) manualBanner.classList.add("d-none");
  const terminal = document.getElementById("portalLogTerminal");
  if (terminal) terminal.innerHTML = "<div>Session reset. Waiting to launch...</div>";
  const resBody = document.getElementById("portalResultsBody");
  if (resBody) resBody.innerHTML = '<tr><td colspan="4" class="text-center text-muted py-2">No results yet.</td></tr>';
  const startBtn = document.getElementById("portalStartBtn");
  if (startBtn) {
    startBtn.disabled = false;
    startBtn.classList.remove("d-none");
    startBtn.innerHTML = '<i class="fa-solid fa-play me-1"></i> Launch Chrome & Verify';
  }
  const cancelBtn = document.getElementById("portalCancelBtn");
  if (cancelBtn) cancelBtn.classList.add("d-none");

  syncPortalScopeData();
  switchPortalView('setup');
}

function syncPortalScopeData() {
  const clientNameEl = document.getElementById("portalActiveClientName");
  const clientGstinEl = document.getElementById("portalActiveClientGstin");
  const clientScopeGstinText = document.getElementById("scopeClientGstinText");
  if (activeClient) {
    if (clientNameEl) clientNameEl.textContent = activeClient.name;
    if (clientGstinEl) clientGstinEl.textContent = activeClient.gstin;
    if (clientScopeGstinText) clientScopeGstinText.textContent = activeClient.gstin;
  } else {
    if (clientNameEl) clientNameEl.textContent = "No Client Selected";
    if (clientGstinEl) clientGstinEl.textContent = "-";
    if (clientScopeGstinText) clientScopeGstinText.textContent = "-";
  }

  const allCount = allPartiesData.length;
  const unverifiedCount = allPartiesData.filter(p => !p.is_verified || p.status === "Unverified" || !p.has_custom_mapping).length;

  const scopeUnverifiedText = document.getElementById("scopeUnverifiedText");
  if (scopeUnverifiedText) {
    scopeUnverifiedText.textContent = `${unverifiedCount} unverified of ${allCount}`;
  }
  const scopePartiesCountText = document.getElementById("scopePartiesCountText");
  if (scopePartiesCountText) {
    scopePartiesCountText.textContent = `${allCount} total counterparties`;
  }

  const portalUserEl = document.getElementById("portalGstUsername");
  const portalPassEl = document.getElementById("portalGstPassword");
  if (portalUserEl) {
    portalUserEl.value = (activeClient && activeClient.gst_username) ? activeClient.gst_username : "";
  }
  if (portalPassEl) {
    portalPassEl.value = (activeClient && activeClient.gst_password) ? activeClient.gst_password : "";
  }

  const unverifiedRadio = document.getElementById("scopeUnverified");
  const allRadio = document.getElementById("scopeCounterparties");
  if (unverifiedCount > 0 && unverifiedRadio) {
    unverifiedRadio.checked = true;
  } else if (allRadio) {
    allRadio.checked = true;
  }
  onVerifyScopeChange();
}

function clearPortalTerminal() {
  const t = document.getElementById("portalLogTerminal");
  if (t) t.innerHTML = "<div>Terminal cleared.</div>";
}

async function openPortalVerificationModal(preselectGstin = null) {
  if (!activeClient) {
    alert("Please select an active taxpayer client before launching GST portal verification.");
    switchToTab("clients-tab");
    return;
  }

  const modalEl = document.getElementById("portalVerificationModal");
  if (!modalEl) return;

  syncPortalScopeData();

  if (preselectGstin) {
    const customRadio = document.getElementById("scopeCustom");
    if (customRadio) customRadio.checked = true;
    const customCont = document.getElementById("customGstinContainer");
    if (customCont) customCont.classList.remove("d-none");
    const customInput = document.getElementById("customGstinInput");
    if (customInput) customInput.value = preselectGstin;
  }

  try {
    const res = await fetch("/api/gstin/verify-portal/status");
    const data = await res.json();
    if (data.is_running) {
      switchPortalView('progress');
      updatePortalUIFromState(data);
      startVerificationPolling();
    } else {
      switchPortalView('setup');
      if (data.results && data.results.length > 0) {
        renderPortalResults(data.results);
      }
    }
  } catch (err) {
    switchPortalView('setup');
  }

  const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
  modal.show();
}

function onVerifyScopeChange() {
  const isCustom = document.getElementById("scopeCustom") ? document.getElementById("scopeCustom").checked : false;
  const customCont = document.getElementById("customGstinContainer");
  if (customCont) {
    if (isCustom) {
      customCont.classList.remove("d-none");
    } else {
      customCont.classList.add("d-none");
    }
  }
}

function verifySingleGstinFromPortal(gstin) {
  openPortalVerificationModal(gstin);
}

async function fetchPortalStatusAndSync() {
  try {
    const res = await fetch("/api/gstin/verify-portal/status");
    const data = await res.json();
    updatePortalUIFromState(data);
    if (data.is_running) {
      startVerificationPolling();
    }
  } catch (err) {
    console.error("Failed to check portal status:", err);
  }
}

async function startPortalVerification() {
  const scopeEl = document.querySelector('input[name="verifyScope"]:checked');
  const scopeVal = scopeEl ? scopeEl.value : "unverified";
  let payload = { mode: scopeVal, gstins: null };

  if (scopeVal === "custom") {
    const raw = document.getElementById("customGstinInput").value;
    const splitGstins = raw.split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(s => s.length === 15);
    if (splitGstins.length === 0) {
      alert("Please enter at least one valid 15-character GSTIN.");
      return;
    }
    payload.gstins = splitGstins;
  }

  const portalUser = document.getElementById("portalGstUsername") ? document.getElementById("portalGstUsername").value.trim() : "";
  const portalPass = document.getElementById("portalGstPassword") ? document.getElementById("portalGstPassword").value.trim() : "";
  const saveCred = document.getElementById("portalSaveCredCheckbox") ? document.getElementById("portalSaveCredCheckbox").checked : true;

  if (portalUser) payload.gst_username = portalUser;
  if (portalPass) payload.gst_password = portalPass;
  payload.save_credentials = saveCred;

  const startBtn = document.getElementById("portalStartBtn");
  startBtn.disabled = true;
  startBtn.innerHTML = `<div class="spinner-border spinner-border-sm me-1"></div> Starting...`;

  try {
    const res = await fetch("/api/gstin/verify-portal/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.detail || "Error starting verification");
      startBtn.disabled = false;
      startBtn.innerHTML = `<i class="fa-solid fa-play me-1"></i> Launch Chrome & Verify`;
      return;
    }

    showToast(`Verification launched for ${data.total} GSTIN(s)! Chrome opening...`);
    switchPortalView('progress');
    const completeBanner = document.getElementById("portalCompleteBanner");
    if (completeBanner) completeBanner.classList.add("d-none");

    startVerificationPolling();
  } catch (err) {
    alert("Network error: " + err.message);
    startBtn.disabled = false;
    startBtn.innerHTML = `<i class="fa-solid fa-play me-1"></i> Launch Chrome & Verify`;
  }
}

function startVerificationPolling() {
  if (verificationPollingInterval) clearInterval(verificationPollingInterval);
  verificationPollingInterval = setInterval(async () => {
    try {
      const res = await fetch("/api/gstin/verify-portal/status");
      const data = await res.json();
      updatePortalUIFromState(data);

      if (!data.is_running && data.status !== "starting") {
        clearInterval(verificationPollingInterval);
        verificationPollingInterval = null;
        await fetchParties();
        await fetchInvoices(currentPage);
        await fetchOverviewData();
      }
    } catch (err) {
      console.error("Polling error:", err);
    }
  }, 1000);
}

function updatePortalUIFromState(state) {
  if (!state) return;

  const pill = document.getElementById("portalStatusPill");
  const pBar = document.getElementById("portalProgressBar");
  const pLabel = document.getElementById("portalProgressLabel");
  const pCounter = document.getElementById("portalProgressCounter");
  const terminal = document.getElementById("portalLogTerminal");
  const manualBanner = document.getElementById("portalManualLoginBanner");
  const completeBanner = document.getElementById("portalCompleteBanner");
  const cancelBtn = document.getElementById("portalCancelBtn");
  const startBtn = document.getElementById("portalStartBtn");

  const statusLabels = {
    "idle": "Idle - Ready to start",
    "starting": "Launching Chrome...",
    "waiting_login": "Waiting for Login in Chrome...",
    "navigating": "Navigating to Search Taxpayer...",
    "verifying": `Extracting: ${state.current_gstin || 'Taxpayers'}...`,
    "completed": "Verification Complete! Names Saved Permanently.",
    "error": "Error encountered",
    "cancelled": "Verification cancelled"
  };

  const statusColors = {
    "idle": "bg-secondary",
    "starting": "bg-info text-dark",
    "waiting_login": "bg-warning text-dark",
    "navigating": "bg-primary",
    "verifying": "bg-teal text-white",
    "completed": "bg-success text-white",
    "error": "bg-danger text-white",
    "cancelled": "bg-dark text-white"
  };

  if (pill) {
    pill.textContent = statusLabels[state.status] || state.status;
    pill.className = `badge ${statusColors[state.status] || 'bg-secondary'}`;
  }

  if (state.is_running && currentPortalTab !== 'progress') {
    switchPortalView('progress');
  }

  const total = state.total || 1;
  const prog = state.progress || 0;
  const pct = Math.min(100, Math.round((prog / total) * 100));
  if (pBar) pBar.style.width = `${pct}%`;
  if (pCounter) pCounter.textContent = `${prog} / ${state.total || 0}`;
  if (pLabel) {
    if (state.status === "verifying") {
      pLabel.textContent = `Verifying ${state.current_gstin}...`;
    } else {
      pLabel.textContent = statusLabels[state.status] || state.status;
    }
  }

  if (manualBanner) {
    if (state.status === "waiting_login") {
      manualBanner.classList.remove("d-none");
    } else {
      manualBanner.classList.add("d-none");
    }
  }

  if (completeBanner) {
    if (state.status === "completed") {
      completeBanner.classList.remove("d-none");
    } else {
      completeBanner.classList.add("d-none");
    }
  }

  if (cancelBtn && startBtn) {
    if (state.is_running) {
      cancelBtn.classList.remove("d-none");
      startBtn.classList.add("d-none");
    } else {
      cancelBtn.classList.add("d-none");
      startBtn.classList.remove("d-none");
      startBtn.disabled = false;
      startBtn.innerHTML = `<i class="fa-solid fa-play me-1"></i> Launch Chrome & Verify`;
    }
  }

  if (terminal && state.logs) {
    terminal.innerHTML = state.logs.map(l => `<div>${l}</div>`).join("");
    terminal.scrollTop = terminal.scrollHeight;
  }

  if (state.results) {
    renderPortalResults(state.results);
  }
}

function renderPortalResults(results) {
  const resultsBody = document.getElementById("portalResultsBody");
  if (!resultsBody) return;
  if (!results || results.length === 0) {
    resultsBody.innerHTML = `<tr><td colspan="4" class="text-center text-muted py-2">No results yet.</td></tr>`;
    return;
  }
  resultsBody.innerHTML = results.map(r => `
    <tr>
      <td class="font-mono fw-bold text-primary">${r.gstin}</td>
      <td class="text-truncate" style="max-width: 220px;" title="${r.legal_name}">${r.legal_name}</td>
      <td class="text-truncate fw-semibold text-dark" style="max-width: 220px;" title="${r.trade_name}">${r.trade_name}</td>
      <td class="text-center">
        ${r.saved ? '<span class="badge bg-success"><i class="fa-solid fa-check me-1"></i>Saved</span>' : `<span class="badge bg-danger">${r.status || 'Failed'}</span>`}
      </td>
    </tr>
  `).join("");
}

async function confirmPortalLogin() {
  try {
    await fetch("/api/gstin/verify-portal/confirm-login", { method: "POST" });
    showToast("Login confirmed! Navigating to Search Taxpayer...");
  } catch (err) {
    console.error("Error confirming login:", err);
  }
}

async function cancelPortalVerification() {
  try {
    await fetch("/api/gstin/verify-portal/cancel", { method: "POST" });
    showToast("Portal verification cancelled.");
    if (verificationPollingInterval) clearInterval(verificationPollingInterval);
    verificationPollingInterval = null;
    fetchPortalStatusAndSync();
  } catch (err) {
    console.error("Error cancelling:", err);
  }
}

function onClosePortalModal() {
  if (verificationPollingInterval) {
    clearInterval(verificationPollingInterval);
    verificationPollingInterval = null;
  }
  switchPortalView('setup');
}

// ---------------------------------------------------------------------------
// 10. Delete Saved Party Mapping
// ---------------------------------------------------------------------------
function deletePartyMappingPrompt(gstin, tradeName) {
  pendingDelPartyGstin = gstin;
  document.getElementById("delPartyGstin").textContent = `GSTIN: ${gstin}`;
  document.getElementById("delPartyNames").textContent = tradeName ? `Trade Name: ${tradeName}` : "";

  const modal = new bootstrap.Modal(document.getElementById("deletePartyModal"));
  modal.show();
}

async function executePartyMappingDelete() {
  if (!pendingDelPartyGstin) return;
  const btn = document.getElementById("confirmDelPartyBtn");
  btn.disabled = true;
  btn.innerHTML = `<div class="spinner-border spinner-border-sm me-1"></div> Deleting...`;

  try {
    const res = await fetch(`/api/parties/${pendingDelPartyGstin}`, { method: "DELETE" });
    const data = await res.json();
    if (res.ok) {
      showToast(`Mapping for ${pendingDelPartyGstin} deleted permanently.`);
      const modal = bootstrap.Modal.getInstance(document.getElementById("deletePartyModal"));
      if (modal) modal.hide();
      await fetchParties();
      await fetchInvoices(currentPage);
    } else {
      alert("Failed to delete mapping: " + (data.detail || "Unknown error"));
    }
  } catch (err) {
    alert("Network error: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<i class="fa-solid fa-trash-can me-1"></i> Yes, Delete Mapping`;
    pendingDelPartyGstin = null;
  }
}

function clearAllPartyMappingsPrompt() {
  const modal = new bootstrap.Modal(document.getElementById("clearAllPartiesModal"));
  modal.show();
}

async function executeClearAllPartyMappings() {
  const btn = document.getElementById("confirmClearAllPartiesBtn");
  btn.disabled = true;
  btn.innerHTML = `<div class="spinner-border spinner-border-sm me-1"></div> Resetting...`;

  try {
    const res = await fetch("/api/parties/clear-all", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      showToast("All saved GSTIN party mappings deleted! Counterparties reset to Unverified.");
      const modal = bootstrap.Modal.getInstance(document.getElementById("clearAllPartiesModal"));
      if (modal) modal.hide();
      await fetchParties();
      await fetchInvoices(currentPage);
    } else {
      alert("Failed to reset mappings: " + (data.detail || "Unknown error"));
    }
  } catch (err) {
    alert("Network error: " + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<i class="fa-solid fa-trash-can me-1"></i> Yes, Reset All Mappings`;
  }
}
