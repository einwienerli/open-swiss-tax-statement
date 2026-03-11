/**
 * Open Swiss Tax Statement - Wizard Controller
 * Guided 6-step wizard: Provider -> Files -> Tax Year -> Details -> Preview -> Complete
 * Uses pywebview JS API bridge (no HTTP server).
 */

class WizardController {
  constructor() {
    this.currentStep = 1;
    this.totalSteps = 6;
    this.previewData = null;

    // File paths (set by native file dialogs)
    this._inputPath = null;
    this._portfolioPath = null;
    this._generatedPdfPath = null;
    this._extractedInfo = null;

    // Tax Year step state
    this._detectedTaxYear = null;
    this._kurslisteStatus = null;

    // Cache DOM elements
    this.form = document.getElementById("generate-form");
    this.steps = document.querySelectorAll("#wizard-steps li");
    this.panels = document.querySelectorAll(".wizard-panel");
    this.errorBanner = document.getElementById("error-banner");
    this.errorList = document.getElementById("error-list");
    this.warningBanner = document.getElementById("warning-banner");
    this.warningList = document.getElementById("warning-list");
    this.previewSection = document.getElementById("preview-section");
    this.previewLoading = document.getElementById("preview-loading");
    this.previewNav = document.getElementById("preview-nav");
    this.previewBtn = document.getElementById("preview-btn");
    this.generateBtn = document.getElementById("generate-btn");
    this.statusBar = document.getElementById("status-bar");

    this._bindNavigation();
    this._bindProviderCards();
    this._bindFileZones();
    this._bindHistory();
    this._bindRequestBroker();
    this._bindFaq();
    this._bindLanguageSelector();
    this._bindProfileButtons();
    this._bindGenerateAnother();
    this._bindSavePdf();

    // Prevent form submission (all navigation is button-driven)
    this.form.addEventListener("submit", (e) => e.preventDefault());
  }

  // --- Initialization (called after pywebview API is ready) -----------------

  async init() {
    try {
      const data = await pywebview.api.get_init_data();

      // Initialize i18n (must be before populating form fields)
      await window.i18n.initI18n();

      // Populate canton dropdown
      const select = document.getElementById("canton-select");
      data.cantons.forEach((c) => {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c;
        select.appendChild(opt);
      });

      // Populate profile fields
      const profile = data.profile;
      if (profile) {
        this._setProfileField("account-number", "account-number-label", profile.account_number);
        this._setProfileFieldByName("first_name", profile.first_name);
        this._setProfileFieldByName("last_name", profile.last_name);
        this._setProfileFieldByName("street", profile.street);
        this._setProfileFieldByName("zip_code", profile.zip_code);
        this._setProfileFieldByName("city", profile.city);
        if (profile.canton) {
          select.value = profile.canton;
          const cantonLabel = select.closest("label");
          if (cantonLabel && profile.canton) {
            cantonLabel.classList.add("auto-populated");
            let hint = cantonLabel.querySelector(".autopop-hint");
            if (!hint) {
              hint = document.createElement("span");
              hint.className = "autopop-hint";
              cantonLabel.appendChild(hint);
            }
            hint.textContent = window.i18n.t("autopop.saved_profile");
          }
        }
      }

      // Set default year
      document.getElementById("tax-year").value = data.default_year;

      // Populate footer
      const footerVersion = document.getElementById("footer-version");
      if (footerVersion && data.version) {
        footerVersion.textContent = `v${data.version}`;
      }
      const footerAuthor = document.getElementById("footer-author");
      if (footerAuthor && data.author) {
        footerAuthor.textContent = data.author;
      }
      const footerLicense = document.getElementById("footer-license");
      if (footerLicense && data.license) {
        footerLicense.textContent = data.license;
      }
      // Enable Load Saved Profile button
      const loadProfileBtn = document.getElementById("load-profile-btn");
      if (loadProfileBtn && data.has_saved_profile) {
        loadProfileBtn.disabled = false;
      }

      // History badge
      const badge = document.getElementById("history-badge");
      badge.textContent = data.statements.length > 0 ? data.statements.length : "";

      // Bind auto-populated clear-on-edit
      this._bindAutoPopulated();

      // Show hero on initial load (step 1)
      const heroSection = document.getElementById("hero-section");
      if (heroSection) heroSection.classList.add("visible");

      // No startup update dialog — user clicks "Check for updates" in footer
    } catch (err) {
      // API not ready or error -- silently continue
    }
  }

  _setProfileField(inputId, labelId, value) {
    if (!value) return;
    const input = document.getElementById(inputId);
    const label = document.getElementById(labelId);
    if (!input || !label) return;
    input.value = value;
    label.classList.add("auto-populated");
    let hint = label.querySelector(".autopop-hint");
    if (!hint) {
      hint = document.createElement("span");
      hint.className = "autopop-hint";
      label.appendChild(hint);
    }
    hint.textContent = window.i18n.t("autopop.saved_profile");
  }

  _setProfileFieldByName(fieldName, value) {
    if (!value) return;
    const input = this.form.querySelector(`[name="${fieldName}"]`);
    if (!input) return;
    const label = input.closest("label");
    if (!label) return;
    input.value = value;
    label.classList.add("auto-populated");
    let hint = label.querySelector(".autopop-hint");
    if (!hint) {
      hint = document.createElement("span");
      hint.className = "autopop-hint";
      label.appendChild(hint);
    }
    hint.textContent = window.i18n.t("autopop.saved_profile");
  }

  // --- Step Navigation ------------------------------------------------------

  goToStep(n) {
    if (n < 1 || n > this.totalSteps) return;

    // Update step indicator
    const isComplete = n === this.totalSteps;
    this.steps.forEach((li) => {
      const s = parseInt(li.dataset.step, 10);
      li.classList.remove("active", "completed");
      if (s < n) li.classList.add("completed");
      if (s === n) li.classList.add(isComplete ? "completed" : "active");
    });

    // Show hero only on step 1
    const heroSection = document.getElementById("hero-section");
    if (heroSection) {
      heroSection.classList.toggle("visible", n === 1);
    }

    // Show correct panel
    this.panels.forEach((p) => p.classList.remove("active"));
    const target = document.getElementById(`panel-${n}`);
    if (target) target.classList.add("active");

    this.currentStep = n;
    this._hideBanners();

    // Initialize Tax Year step when entering step 3
    if (n === 3) {
      this._initTaxYearStep();
    }

    // If going back from step 5+, invalidate preview data
    if (n < 5) {
      this.previewData = null;
      this.previewSection.hidden = true;
      this.previewNav.hidden = true;
      this.previewLoading.hidden = true;
    }

    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  _bindNavigation() {
    document.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action]");
      if (!btn) return;

      const action = btn.dataset.action;
      if (action === "next") {
        if (this._validateStep(this.currentStep)) {
          // Show warning dialog if leaving step 3 without kursliste
          if (this.currentStep === 3 && !this._isKurslisteReady()) {
            this._showKurslisteWarning();
            return;
          }
          this.goToStep(this.currentStep + 1);
        }
      } else if (action === "back") {
        this.goToStep(this.currentStep - 1);
      } else if (action === "preview") {
        if (this._validateStep(this.currentStep)) {
          this._runPreview();
        }
      } else if (action === "generate") {
        this._runGenerate();
      }
    });
  }

  // --- Validation -----------------------------------------------------------

  _validateStep(step) {
    const errors = [];

    if (step === 1) {
      const provider = this.form.querySelector('input[name="provider"]:checked');
      if (!provider) errors.push(window.i18n.t("validation.provider_required"));
    }

    if (step === 2) {
      if (!this._inputPath) errors.push(window.i18n.t("validation.input_required"));
      if (!this._portfolioPath) errors.push(window.i18n.t("validation.portfolio_required"));
    }

    if (step === 3) {
      if (!this._detectedTaxYear) {
        errors.push(window.i18n.t("validation.tax_year_needed"));
      }
    }

    if (step === 4) {
      const fields = [
        { name: "account_number", key: "step3.label.account_number" },
        { name: "tax_year", key: "step3.label.tax_year" },
        { name: "first_name", key: "step3.label.first_name" },
        { name: "last_name", key: "step3.label.last_name" },
        { name: "street", key: "step3.label.street" },
        { name: "zip_code", key: "step3.label.zip" },
        { name: "city", key: "step3.label.city" },
        { name: "canton", key: "step3.label.canton" },
      ];

      fields.forEach(({ name, key }) => {
        const el = this.form.querySelector(`[name="${name}"]`);
        if (!el || !el.value.trim()) {
          const label = window.i18n.t(key);
          errors.push(window.i18n.t("validation.field_required").replace("{field}", label));
        }
      });

      // Validate tax year range
      const yearEl = this.form.querySelector('[name="tax_year"]');
      if (yearEl && yearEl.value.trim()) {
        const y = parseInt(yearEl.value, 10);
        if (isNaN(y) || y < 2000 || y > 2099) {
          errors.push(window.i18n.t("validation.year_range"));
        }
      }

      // Validate AHV if provided
      const ahv = this.form.querySelector('[name="ahv_number"]');
      if (ahv && ahv.value.trim()) {
        if (!/^756\.\d{4}\.\d{4}\.\d{2}$/.test(ahv.value.trim())) {
          errors.push(window.i18n.t("validation.ahv_format"));
        }
      }
    }

    if (errors.length > 0) {
      this._showErrors(errors);
      return false;
    }
    return true;
  }

  // --- File Selection (native dialogs) --------------------------------------

  _bindFileZones() {
    const inputZone = document.getElementById("input-zone");
    const portfolioZone = document.getElementById("portfolio-zone");

    inputZone.addEventListener("click", () => this._selectTransactionFile());
    portfolioZone.addEventListener("click", () => this._selectPortfolioFile());
  }

  async _selectTransactionFile() {
    try {
      const result = await pywebview.api.select_transaction_file();
      if (result) {
        this._inputPath = result.path;
        this._markFileSelected("input-zone", "input-label", result.filename);
        this._setStatus("Transaction file selected: " + result.filename);

        // Try extracting account from filename as fallback
        const match = result.filename.match(/_(\d{5,10})_/);
        if (match) {
          this._setFieldAutoPopulated("account-number", "account-number-label", match[1], window.i18n.t("autopop.filename"));
        }

        // For Kontoauszug PDFs, auto-populate form from extracted info
        if (result.info) {
          this._applyExtractedInfo(result.info, result.filename);
        }

        // Invalidate preview when files change
        this.previewData = null;
      }
    } catch (err) {
      this._setStatus("Error selecting file: " + err.message);
    }
  }

  async _selectPortfolioFile() {
    try {
      const result = await pywebview.api.select_portfolio_file();
      if (result) {
        this._portfolioPath = result.path;
        this._markFileSelected("portfolio-zone", "portfolio-label", result.filename);

        // Auto-populate form fields from extracted info
        if (result.info) {
          this._applyExtractedInfo(result.info, result.filename);
        } else {
          this._setStatus("Portfolio selected: " + result.filename);
        }

        // Invalidate preview when files change
        this.previewData = null;
      }
    } catch (err) {
      this._setStatus("Error selecting portfolio: " + err.message);
    }
  }

  _applyExtractedInfo(info, filename) {
    // Store for "Load from Files" button
    this._extractedInfo = info;
    const loadFromFilesBtn = document.getElementById("load-from-files-btn");
    if (loadFromFilesBtn && info && Object.keys(info).length > 0) {
      loadFromFilesBtn.disabled = false;
    }

    const pdfHint = window.i18n.t("autopop.uploaded_pdf");
    if (info.account_number) {
      this._setFieldAutoPopulated("account-number", "account-number-label", info.account_number, pdfHint);
    }
    if (info.first_name) {
      this._setFieldAutoPopulatedByName("first_name", info.first_name, pdfHint);
    }
    if (info.last_name) {
      this._setFieldAutoPopulatedByName("last_name", info.last_name, pdfHint);
    }
    if (info.street) {
      this._setFieldAutoPopulatedByName("street", info.street, pdfHint);
    }
    if (info.zip_code) {
      this._setFieldAutoPopulatedByName("zip_code", info.zip_code, pdfHint);
    }
    if (info.city) {
      this._setFieldAutoPopulatedByName("city", info.city, pdfHint);
    }
    if (info.year) {
      this._setFieldAutoPopulated("tax-year", "tax-year-label", info.year.toString(), pdfHint);
    }

    const parts = [];
    if (info.first_name && info.last_name) parts.push(info.first_name + " " + info.last_name);
    if (info.account_number) parts.push("Account: " + info.account_number);
    this._setStatus("Portfolio selected: " + filename + (parts.length ? " \u00b7 " + parts.join(", ") : ""));
  }

  _markFileSelected(zoneId, labelId, filename) {
    const zone = document.getElementById(zoneId);
    const label = document.getElementById(labelId);
    label.textContent = filename;
    zone.classList.add("has-file");
    const hint = zone.querySelector(".zone-hint");
    if (hint) hint.textContent = window.i18n.t("step2.file_selected");
  }

  // --- Preview --------------------------------------------------------------

  async _runPreview() {
    this.goToStep(5);
    this.previewLoading.hidden = false;
    this.previewSection.hidden = true;
    this.previewNav.hidden = true;
    this._setStatus("Parsing and enriching...");

    try {
      const data = this._collectFormData();
      const result = await pywebview.api.preview(data);

      if (result.success) {
        this.previewData = result;
        this._renderPreview(result.summary);
        this.previewSection.hidden = false;
        this.previewNav.hidden = false;
        this._showWarnings(result.warnings || []);

        const t = result.summary.totals;
        this._setStatus(
          `Preview ready \u2014 ${t.holdings_count} holdings, ${t.cash_count} cash balances, ${t.payments_count} payments`
        );
      } else {
        this._showErrors(result.errors || [window.i18n.t("error.unknown")]);
        this._setStatus("Error: " + (result.errors || ["Unknown"])[0]);
      }
    } catch (err) {
      this._showErrors(["Error: " + err.message]);
      this._setStatus("Error: " + err.message);
    } finally {
      this.previewLoading.hidden = true;
    }
  }

  // --- Generate -------------------------------------------------------------

  async _runGenerate() {
    this._hideBanners();
    this.generateBtn.setAttribute("aria-busy", "true");
    this.generateBtn.textContent = window.i18n.t("btn.generating");
    this.generateBtn.disabled = true;
    this._setStatus("Generating PDF...");

    try {
      const data = this._collectFormData();
      const result = await pywebview.api.generate(data);

      if (result.success) {
        this._generatedPdfPath = result.output_path;
        document.getElementById("download-filename").textContent = result.filename;
        this._showWarnings(result.warnings || []);
        this._setStatus("PDF generated: " + result.filename);

        // Advance to complete step
        this.goToStep(6);

        // Refresh history
        this._refreshStatements();
      } else {
        this._showErrors(result.errors || [window.i18n.t("error.unknown")]);
        this._setStatus("Error: " + (result.errors || ["Unknown"])[0]);
      }
    } catch (err) {
      this._showErrors(["Error: " + err.message]);
      this._setStatus("Error: " + err.message);
    } finally {
      this.generateBtn.removeAttribute("aria-busy");
      this.generateBtn.textContent = window.i18n.t("btn.generate");
      this.generateBtn.disabled = false;
    }
  }

  // --- Save PDF -------------------------------------------------------------

  _bindSavePdf() {
    const btn = document.getElementById("save-pdf-btn");
    if (btn) {
      btn.addEventListener("click", async () => {
        if (this._generatedPdfPath) {
          try {
            await pywebview.api.save_pdf(this._generatedPdfPath);
          } catch (err) {
            this._setStatus("Error saving PDF: " + err.message);
          }
        }
      });
    }
  }

  // --- Form Data Collection -------------------------------------------------

  _collectFormData() {
    return {
      provider: this.form.querySelector('input[name="provider"]:checked')?.value || "swissquote",
      account_number: this.form.querySelector('[name="account_number"]').value.trim(),
      tax_year: this.form.querySelector('[name="tax_year"]').value.trim(),
      first_name: this.form.querySelector('[name="first_name"]').value.trim(),
      last_name: this.form.querySelector('[name="last_name"]').value.trim(),
      street: this.form.querySelector('[name="street"]').value.trim(),
      zip_code: this.form.querySelector('[name="zip_code"]').value.trim(),
      city: this.form.querySelector('[name="city"]').value.trim(),
      canton: this.form.querySelector('[name="canton"]').value.trim(),
      ahv_number: this.form.querySelector('[name="ahv_number"]')?.value.trim() || "",
      save_profile: this.form.querySelector('[name="save_profile"]')?.checked || false,
      input_path: this._inputPath,
      portfolio_path: this._portfolioPath,
    };
  }

  // --- Provider Cards -------------------------------------------------------

  _bindProviderCards() {
    document.querySelectorAll(".provider-card:not(.disabled):not(.request-card)").forEach((card) => {
      card.addEventListener("click", () => {
        document.querySelectorAll(".provider-card").forEach((c) => c.classList.remove("selected"));
        card.classList.add("selected");
        const radio = card.querySelector('input[type="radio"]');
        if (radio) radio.checked = true;
      });
    });
  }

  // --- Preview Rendering ----------------------------------------------------

  _renderPreview(summary) {
    // Summary cards
    document.getElementById("count-holdings").textContent = summary.totals.holdings_count;
    document.getElementById("count-cash").textContent = summary.totals.cash_count;
    document.getElementById("count-payments").textContent = summary.totals.payments_count;
    document.getElementById("count-expenses").textContent = summary.totals.expenses_count;
    document.getElementById("count-trades").textContent =
      summary.trades ? summary.trades.length : 0;

    // Holdings
    const holdingsTbody = document.querySelector("#holdings-table tbody");
    holdingsTbody.innerHTML = summary.holdings
      .map(
        (h) =>
          `<tr>
            <td>${escapeHtml(h.name)}</td>
            <td>${escapeHtml(h.isin)}</td>
            <td class="numeric">${formatSwiss(h.quantity)}</td>
            <td>${escapeHtml(h.currency)}</td>
            <td class="numeric">${formatSwiss(h.tax_value_chf)}</td>
          </tr>`
      )
      .join("");
    document.getElementById("holdings-summary").textContent =
      window.i18n.t("step4.summary.holdings") + " (" + summary.holdings.length + ")";

    // Cash
    const cashTbody = document.querySelector("#cash-table tbody");
    cashTbody.innerHTML = summary.cash_balances
      .map(
        (c) =>
          `<tr>
            <td>${escapeHtml(c.currency)}</td>
            <td class="numeric">${formatSwiss(c.balance)}</td>
            <td class="numeric">${formatSwiss(c.tax_value_chf)}</td>
            <td class="numeric">${formatSwiss(c.interest)}</td>
          </tr>`
      )
      .join("");
    document.getElementById("cash-summary").textContent =
      window.i18n.t("step4.summary.cash") + " (" + summary.cash_balances.length + ")";

    // Payments
    const paymentsTbody = document.querySelector("#payments-table tbody");
    paymentsTbody.innerHTML = summary.security_payments
      .map(
        (p) =>
          `<tr>
            <td>${escapeHtml(p.name)}</td>
            <td>${escapeHtml(p.date)}</td>
            <td class="numeric">${formatSwiss(p.gross)}</td>
            <td>${escapeHtml(p.currency)}</td>
            <td>${escapeHtml(p.category)}</td>
            <td class="numeric">${formatSwiss(p.wht)}</td>
            <td class="numeric">${formatSwiss(p.net)}</td>
          </tr>`
      )
      .join("");
    document.getElementById("payments-summary").textContent =
      window.i18n.t("step4.summary.payments") + " (" + summary.security_payments.length + ")";

    // Expenses
    const expensesDetails = document.getElementById("expenses-details");
    if (summary.expenses.length > 0) {
      expensesDetails.hidden = false;
      const expensesTbody = document.querySelector("#expenses-table tbody");
      expensesTbody.innerHTML = summary.expenses
        .map(
          (e) =>
            `<tr>
              <td>${escapeHtml(e.name)}</td>
              <td>${escapeHtml(e.date)}</td>
              <td class="numeric">${formatSwiss(e.amount)}</td>
              <td>${escapeHtml(e.currency)}</td>
            </tr>`
        )
        .join("");
      document.getElementById("expenses-summary").textContent =
        window.i18n.t("step4.summary.expenses") + " (" + summary.expenses.length + ")";
    } else {
      expensesDetails.hidden = true;
    }

    // Trades
    const tradesDetails = document.getElementById("trades-details");
    if (summary.trades && summary.trades.length > 0) {
      tradesDetails.hidden = false;
      const tradesTbody = document.querySelector("#trades-table tbody");
      tradesTbody.innerHTML = summary.trades
        .map(
          (t) =>
            `<tr>
              <td>${escapeHtml(t.name)}</td>
              <td>${escapeHtml(t.isin)}</td>
              <td>${escapeHtml(t.currency)}</td>
              <td class="numeric">${formatSwiss(t.qty_bought)}</td>
              <td class="numeric">${formatSwiss(t.qty_sold)}</td>
              <td class="numeric">${formatSwiss(t.net_change)}</td>
            </tr>`
        )
        .join("");
      document.getElementById("trades-summary").textContent =
        window.i18n.t("step4.summary.trades") + " (" + summary.trades.length + ")";
    } else {
      tradesDetails.hidden = true;
    }
  }

  // --- History Dialog -------------------------------------------------------

  _bindHistory() {
    const dialog = document.getElementById("history-dialog");
    const openBtn = document.getElementById("history-btn");
    const closeBtn = document.getElementById("dialog-close");

    openBtn.addEventListener("click", () => {
      this._refreshStatements();
      dialog.showModal();
    });

    closeBtn.addEventListener("click", () => dialog.close());

    // Close on backdrop click
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) dialog.close();
    });
  }

  _bindDeleteButtons() {
    document.querySelectorAll(".delete-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const folder = btn.dataset.folder;
        const confirmMsg = window.i18n.t("dialog.history.confirm_delete").replace("{folder}", folder);
        if (!confirm(confirmMsg)) return;

        try {
          const result = await pywebview.api.delete_statement(folder);
          if (result.success) {
            this._setStatus("Deleted: " + folder);
            this._refreshStatements();
          } else {
            this._setStatus("Error: " + (result.error || "Could not delete"));
          }
        } catch (err) {
          this._setStatus("Error: " + err.message);
        }
      });
    });
  }

  _bindDownloadButtons() {
    document.querySelectorAll(".download-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const folder = btn.dataset.folder;
        const filename = btn.dataset.filename;
        try {
          await pywebview.api.download_statement(folder, filename);
        } catch (err) {
          this._setStatus("Error: " + err.message);
        }
      });
    });

    document.querySelectorAll(".zip-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const folder = btn.dataset.folder;
        try {
          await pywebview.api.download_folder_zip(folder);
        } catch (err) {
          this._setStatus("Error: " + err.message);
        }
      });
    });
  }

  async _refreshStatements() {
    try {
      const statements = await pywebview.api.list_statements();

      const dialogBody = document.getElementById("dialog-body");
      const badge = document.getElementById("history-badge");

      badge.textContent = statements.length > 0 ? statements.length : "";

      if (statements.length === 0) {
        dialogBody.innerHTML = '<div class="dialog-empty">' + escapeHtml(window.i18n.t("dialog.history.empty")) + '</div>';
        return;
      }

      const providerNames = { sq: "Swissquote", ib: "Interactive Brokers", dg: "Degiro" };
      dialogBody.innerHTML = `
        <table class="statements-table">
          <thead>
            <tr>
              <th>${escapeHtml(window.i18n.t("dialog.history.col.provider"))}</th>
              <th>${escapeHtml(window.i18n.t("dialog.history.col.year"))}</th>
              <th>${escapeHtml(window.i18n.t("dialog.history.col.account"))}</th>
              <th class="actions-col">${escapeHtml(window.i18n.t("dialog.history.col.actions"))}</th>
            </tr>
          </thead>
          <tbody>
            ${statements
              .map(
                (s) => `
              <tr>
                <td>${escapeHtml(providerNames[s.provider] || s.provider)}</td>
                <td>${s.year}</td>
                <td>${escapeHtml(s.account)}</td>
                <td class="actions-col">${
                  s.has_output
                    ? `<button type="button" class="download-btn btn-sm btn-primary" data-folder="${escapeHtml(s.folder)}" data-filename="${escapeHtml(s.output_file)}">${escapeHtml(window.i18n.t("dialog.history.btn.download"))}</button>`
                    : ""
                }
                  <button type="button" class="zip-btn btn-sm btn-secondary" data-folder="${escapeHtml(s.folder)}">${escapeHtml(window.i18n.t("dialog.history.btn.zip"))}</button>
                  <button type="button" class="delete-btn btn-sm btn-danger" data-folder="${escapeHtml(s.folder)}">${escapeHtml(window.i18n.t("dialog.history.btn.delete"))}</button>
                </td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>`;

      this._bindDeleteButtons();
      this._bindDownloadButtons();
    } catch (err) {
      // Silently ignore refresh errors
    }
  }

  // --- Request Broker Dialog ------------------------------------------------

  _bindRequestBroker() {
    const dialog = document.getElementById("request-dialog");
    const openBtn = document.getElementById("request-broker-card");
    const closeBtn = document.getElementById("request-dialog-close");

    openBtn.addEventListener("click", () => dialog.showModal());
    closeBtn.addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) dialog.close();
    });
  }

  // --- FAQ Dialog -----------------------------------------------------------

  _bindFaq() {
    const dialog = document.getElementById("faq-dialog");
    const openBtn = document.getElementById("faq-btn");
    const closeBtn = document.getElementById("faq-dialog-close");

    openBtn.addEventListener("click", () => dialog.showModal());
    closeBtn.addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) dialog.close();
    });
  }

  // --- Language Selector ----------------------------------------------------

  _bindLanguageSelector() {
    document.querySelectorAll(".lang-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const lang = btn.dataset.lang;
        if (lang && lang !== window.i18n.getCurrentLang()) {
          window.i18n.setLanguage(lang);
        }
      });
    });
  }

  // --- Profile Buttons ------------------------------------------------------

  _bindProfileButtons() {
    const loadProfileBtn = document.getElementById("load-profile-btn");
    const loadFromFilesBtn = document.getElementById("load-from-files-btn");
    const clearBtn = document.getElementById("clear-fields-btn");

    if (loadProfileBtn) {
      loadProfileBtn.addEventListener("click", async () => {
        try {
          const profile = await pywebview.api.get_profile();
          if (profile) this._populateFormFromProfile(profile);
        } catch (e) { /* silently fail */ }
      });
    }

    if (loadFromFilesBtn) {
      loadFromFilesBtn.addEventListener("click", () => {
        if (this._extractedInfo) {
          this._populateFormFromInfo(this._extractedInfo);
        }
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        this._clearFormFields();
      });
    }
  }

  _populateFormFromProfile(profile) {
    this._setProfileField("account-number", "account-number-label", profile.account_number);
    this._setProfileFieldByName("first_name", profile.first_name);
    this._setProfileFieldByName("last_name", profile.last_name);
    this._setProfileFieldByName("street", profile.street);
    this._setProfileFieldByName("zip_code", profile.zip_code);
    this._setProfileFieldByName("city", profile.city);
    const select = document.getElementById("canton-select");
    if (select && profile.canton) {
      select.value = profile.canton;
    }
    if (profile.ahv_number) {
      this._setProfileFieldByName("ahv_number", profile.ahv_number);
    }
  }

  _populateFormFromInfo(info) {
    if (info.first_name) this._setProfileFieldByName("first_name", info.first_name);
    if (info.last_name) this._setProfileFieldByName("last_name", info.last_name);
    if (info.street) this._setProfileFieldByName("street", info.street);
    if (info.zip_code) this._setProfileFieldByName("zip_code", info.zip_code);
    if (info.city) this._setProfileFieldByName("city", info.city);
    if (info.account_number) {
      this._setProfileField("account-number", "account-number-label", info.account_number);
    }
    if (info.year) {
      document.getElementById("tax-year").value = info.year;
    }
  }

  _clearFormFields() {
    const fields = ["account_number", "first_name", "last_name", "street", "zip_code", "city", "ahv_number"];
    fields.forEach((name) => {
      const el = this.form.querySelector(`[name="${name}"]`);
      if (el) el.value = "";
    });
    const acctInput = document.getElementById("account-number");
    if (acctInput) acctInput.value = "";
    const canton = document.getElementById("canton-select");
    if (canton) canton.value = "";
    const taxYear = document.getElementById("tax-year");
    if (taxYear) taxYear.value = "";
    // Remove auto-populated hints
    this.form.querySelectorAll(".auto-populated").forEach((el) => {
      el.classList.remove("auto-populated");
      const hint = el.querySelector(".autopop-hint");
      if (hint) hint.remove();
    });
  }

  // --- Generate Another -----------------------------------------------------

  _bindGenerateAnother() {
    const btn = document.getElementById("generate-another-btn");
    if (btn) {
      btn.addEventListener("click", () => {
        // Reset file paths
        this._inputPath = null;
        this._portfolioPath = null;
        this._generatedPdfPath = null;

        // Reset file zones
        document.querySelectorAll(".drop-zone").forEach((zone) => {
          zone.classList.remove("has-file");
        });
        document.getElementById("input-label").textContent = window.i18n.t("step2.input_label");
        document.getElementById("portfolio-label").textContent = window.i18n.t("step2.portfolio_label");

        // Reset zone hints
        const inputHint = document.querySelector("#input-zone .zone-hint");
        if (inputHint) inputHint.textContent = window.i18n.t("step2.input_hint");
        const portfolioHint = document.querySelector("#portfolio-zone .zone-hint");
        if (portfolioHint) portfolioHint.textContent = window.i18n.t("step2.portfolio_hint");

        this.previewData = null;
        this._setStatus("Ready");
        this.goToStep(1);
      });
    }
  }

  // --- Auto-populated Field Highlights --------------------------------------

  _bindAutoPopulated() {
    document.querySelectorAll(".auto-populated").forEach((label) => {
      const input = label.querySelector("input, select");
      if (!input) return;
      if (input.dataset.autopopBound) return;
      input.dataset.autopopBound = "1";
      const clearHighlight = () => {
        label.classList.remove("auto-populated");
        const hint = label.querySelector(".autopop-hint");
        if (hint) hint.remove();
      };
      input.addEventListener("input", clearHighlight);
      input.addEventListener("change", clearHighlight);
    });
  }

  _setFieldAutoPopulated(inputId, labelId, value, hintText) {
    const input = document.getElementById(inputId);
    const label = document.getElementById(labelId);
    if (!input || !label) return;
    input.value = value;
    label.classList.add("auto-populated");
    let hint = label.querySelector(".autopop-hint");
    if (!hint) {
      hint = document.createElement("span");
      hint.className = "autopop-hint";
      label.appendChild(hint);
    }
    hint.textContent = hintText;
    if (!input.dataset.autopopBound) {
      input.dataset.autopopBound = "1";
      const clearHighlight = () => {
        label.classList.remove("auto-populated");
        const h = label.querySelector(".autopop-hint");
        if (h) h.remove();
      };
      input.addEventListener("input", clearHighlight);
      input.addEventListener("change", clearHighlight);
    }
  }

  _setFieldAutoPopulatedByName(fieldName, value, hintText) {
    const input = this.form.querySelector(`[name="${fieldName}"]`);
    if (!input) return;
    const label = input.closest("label");
    if (!label) return;
    // Only auto-populate if field is empty or already auto-populated
    if (input.value.trim() && !label.classList.contains("auto-populated")) return;
    input.value = value;
    label.classList.add("auto-populated");
    let hint = label.querySelector(".autopop-hint");
    if (!hint) {
      hint = document.createElement("span");
      hint.className = "autopop-hint";
      label.appendChild(hint);
    }
    hint.textContent = hintText;
    if (!input.dataset.autopopBound) {
      input.dataset.autopopBound = "1";
      const clearHighlight = () => {
        label.classList.remove("auto-populated");
        const h = label.querySelector(".autopop-hint");
        if (h) h.remove();
      };
      input.addEventListener("input", clearHighlight);
      input.addEventListener("change", clearHighlight);
    }
  }

  // --- Tax Year Step --------------------------------------------------------

  async _initTaxYearStep() {
    // Detect tax year from extracted info or form field
    const yearEl = document.getElementById("tax-year");
    const year = this._extractedInfo?.year || (yearEl ? parseInt(yearEl.value, 10) : null);

    if (!year || isNaN(year)) {
      document.getElementById("detected-tax-year").textContent = "\u2014";
      this._detectedTaxYear = null;
      return;
    }

    this._detectedTaxYear = year;
    document.getElementById("detected-tax-year").textContent = year;

    // Check kursliste availability
    this._showKurslisteState("checking");

    try {
      const status = await pywebview.api.check_kursliste(year);
      this._kurslisteStatus = status;

      if (status.available) {
        const textKey = status.source === "user"
          ? "step3_taxyear.kursliste_user"
          : "step3_taxyear.kursliste_bundled";
        document.getElementById("kursliste-available-text").textContent = window.i18n.t(textKey);
        this._showKurslisteState("available");
      } else {
        this._showKurslisteState("missing");
        this._bindKurslisteActions();
      }
    } catch (err) {
      this._showKurslisteState("missing");
      this._bindKurslisteActions();
    }
  }

  _showKurslisteState(state) {
    const states = ["checking", "available", "missing", "processing", "processed"];
    states.forEach((s) => {
      const el = document.getElementById(`kursliste-${s}`);
      if (el) el.hidden = s !== state;
    });

    // Substitute {year} in the missing header text
    if (state === "missing" && this._detectedTaxYear) {
      const el = document.getElementById("kursliste-missing-text");
      if (el) {
        el.textContent = window.i18n.t("step3_taxyear.kursliste_missing").replace("{year}", this._detectedTaxYear);
      }
    }

    // Toggle Continue button style: yellow caution when kursliste not loaded
    const continueBtn = document.getElementById("step3-continue-btn");
    if (continueBtn) {
      const kurslisteReady = state === "available" || state === "processed";
      continueBtn.classList.toggle("btn-caution", !kurslisteReady);
    }
  }

  _bindKurslisteActions() {
    const btn = document.getElementById("select-kursliste-btn");
    if (!btn || btn.dataset.bound) return;
    btn.dataset.bound = "1";

    btn.addEventListener("click", async () => {
      try {
        const path = await pywebview.api.select_kursliste_file();
        if (!path) return;

        this._showKurslisteState("processing");

        const result = await pywebview.api.process_kursliste(this._detectedTaxYear, path);
        if (result.success) {
          const countText = window.i18n.t("step3_taxyear.kursliste_count").replace("{count}", result.count);
          document.getElementById("kursliste-count").textContent = `\u2014 ${countText}`;
          this._showKurslisteState("processed");
          this._kurslisteStatus = { available: true, source: "user" };
        } else {
          this._showKurslisteState("missing");
          this._showErrors([result.error || window.i18n.t("error.unknown")]);
        }
      } catch (err) {
        this._showKurslisteState("missing");
        this._showErrors(["Error: " + err.message]);
      }
    });
  }

  _isKurslisteReady() {
    return this._kurslisteStatus && this._kurslisteStatus.available;
  }

  _showKurslisteWarning() {
    const dialog = document.getElementById("kursliste-warning-dialog");
    if (!dialog) return;

    // Substitute {year} in title and text
    const year = this._detectedTaxYear || "";
    const titleEl = document.getElementById("kursliste-warning-title");
    if (titleEl) {
      titleEl.textContent = window.i18n.t("step3_taxyear.warning_title").replace("{year}", year);
    }
    const textEl = document.getElementById("kursliste-warning-text");
    if (textEl) {
      textEl.textContent = window.i18n.t("step3_taxyear.warning_text").replace("{year}", year);
    }

    const backBtn = document.getElementById("kursliste-warning-back");
    const continueBtn = document.getElementById("kursliste-warning-continue");

    const closeDialog = () => {
      dialog.close();
      backBtn.replaceWith(backBtn.cloneNode(true));
      continueBtn.replaceWith(continueBtn.cloneNode(true));
    };

    document.getElementById("kursliste-warning-back").addEventListener("click", () => {
      closeDialog();
    });

    document.getElementById("kursliste-warning-continue").addEventListener("click", () => {
      closeDialog();
      this.goToStep(this.currentStep + 1);
    });

    dialog.addEventListener("click", (e) => {
      if (e.target === dialog) closeDialog();
    });

    dialog.showModal();
  }

  // --- Helpers --------------------------------------------------------------

  _setStatus(message) {
    this.statusBar.textContent = message;
  }

  _showErrors(errors) {
    this.errorList.innerHTML = "";
    errors.forEach((msg) => {
      const li = document.createElement("li");
      li.textContent = msg;
      this.errorList.appendChild(li);
    });
    this.errorBanner.hidden = false;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  _showWarnings(warnings) {
    if (!warnings || warnings.length === 0) {
      this.warningBanner.hidden = true;
      return;
    }
    this.warningList.innerHTML = "";
    warnings.forEach((msg) => {
      const li = document.createElement("li");
      li.textContent = msg;
      this.warningList.appendChild(li);
    });
    this.warningBanner.hidden = false;
  }

  _hideBanners() {
    this.errorBanner.hidden = true;
    this.warningBanner.hidden = true;
  }
}

// --- Standalone Utilities ---------------------------------------------------

function formatSwiss(value) {
  if (value == null) return "\u2014";
  const num = parseFloat(value);
  if (isNaN(num)) return escapeHtml(value);
  const parts = num.toFixed(2).split(".");
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, "\u2019");
  return parts.join(".");
}

function escapeHtml(text) {
  if (text == null) return "";
  const div = document.createElement("div");
  div.textContent = String(text);
  return div.innerHTML;
}

// --- Initialize -------------------------------------------------------------

window.addEventListener("pywebviewready", async () => {
  const controller = new WizardController();
  window.app = controller;
  await controller.init();

  // Restore wizard step after language switch reload
  const savedStep = sessionStorage.getItem("wizardStep");
  if (savedStep) {
    sessionStorage.removeItem("wizardStep");
    const step = parseInt(savedStep, 10);
    if (step > 1 && step <= controller.totalSteps) {
      controller.goToStep(step);
    }
  }
});
