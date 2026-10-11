(function () {
  "use strict";

  // ---------- Config ----------
  var DEFAULTS = {
    company: "Storm Restoration",
    color: "#B91C1C",
    apiBaseUrl: "https://api.stormbook.ai",
    mock: true,
  };
  var SUBMIT_TIMEOUT_MS = 12000;
  var MAX_PHOTOS = 5;
  var MAX_PHOTO_BYTES = 5 * 1024 * 1024;
  var ALLOWED_PHOTO_TYPES = ["image/jpeg", "image/png"];
  var HEX_RE = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  var ALLOWED_SOURCES = ["meta", "angi", "direct", "web"];

  // ---------- Boot ----------
  document.addEventListener("DOMContentLoaded", function () {
    loadConfig().then(start).catch(function (err) {
      console.error("[stormbook-intake] config load failed", err);
      // Fall back to baked-in defaults; form still works in production-like mode.
      start(DEFAULTS);
    });
  });

  function loadConfig() {
    // Fetch will fail on file:// (no http). Catch and return defaults.
    if (location.protocol === "file:") return Promise.resolve(DEFAULTS);
    return fetch("config.json", { cache: "no-cache" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (live) {
        if (live) return mergeConfig(DEFAULTS, live);
        return fetch("config.example.json", { cache: "no-cache" })
          .then(function (r) { return r.ok ? r.json() : DEFAULTS; })
          .catch(function () { return DEFAULTS; })
          .then(function (ex) { return mergeConfig(DEFAULTS, ex); });
      });
  }

  function mergeConfig(base, override) {
    var out = {};
    for (var k in base) out[k] = base[k];
    if (override && typeof override === "object") {
      for (var k2 in override) if (override[k2] !== undefined) out[k2] = override[k2];
    }
    return out;
  }

  function start(config) {
    var params = parseParams();

    if (!params.client_id) {
      showFatal();
      return;
    }

    applyBranding(params, config);
    wireForm(params, config);
  }

  // ---------- URL params ----------
  function parseParams() {
    var qs = new URLSearchParams(window.location.search);
    var clientId = (qs.get("client_id") || "").trim();
    var company = (qs.get("company") || "").trim();
    var colorRaw = (qs.get("color") || "").trim();
    var color = HEX_RE.test(colorRaw) ? colorRaw : "";
    var logo = (qs.get("logo") || "").trim();
    var phone = (qs.get("phone") || "").trim();
    var sourceRaw = (qs.get("source") || "").trim().toLowerCase();
    var source = ALLOWED_SOURCES.indexOf(sourceRaw) >= 0 ? sourceRaw : "direct";

    return {
      client_id: clientId,
      company: company,
      color: color,
      logo: logo,
      phone: phone,
      source: source,
    };
  }

  // ---------- Branding ----------
  function applyBranding(params, config) {
    var company = params.company || config.defaultCompany || DEFAULTS.company;
    var color = params.color || config.defaultColor || DEFAULTS.color;

    document.documentElement.style.setProperty("--primary", color);
    document.documentElement.style.setProperty("--primary-hover", darken(color, 0.15));

    var companyEl = document.getElementById("brand-company");
    if (companyEl) companyEl.textContent = company;
    // The consent label names the business that will actually be texting.
    var consentCompanyEl = document.getElementById("consent-company");
    if (consentCompanyEl) consentCompanyEl.textContent = company;
    document.title = "Free storm damage inspection — " + company;

    if (params.logo) {
      var img = document.getElementById("brand-logo");
      img.onload = function () { img.hidden = false; };
      img.onerror = function () { img.hidden = true; };
      img.alt = company + " logo";
      img.src = params.logo;
    }
  }

  function darken(hex, amount) {
    var h = hex.replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var r = parseInt(h.slice(0, 2), 16);
    var g = parseInt(h.slice(2, 4), 16);
    var b = parseInt(h.slice(4, 6), 16);
    r = Math.max(0, Math.round(r * (1 - amount)));
    g = Math.max(0, Math.round(g * (1 - amount)));
    b = Math.max(0, Math.round(b * (1 - amount)));
    return "#" + [r, g, b].map(function (v) { return v.toString(16).padStart(2, "0"); }).join("");
  }

  function showFatal() {
    document.getElementById("form-view").hidden = true;
    document.getElementById("success-view").hidden = true;
    document.getElementById("fatal").hidden = false;
  }

  // ---------- Form wiring ----------
  function wireForm(params, config) {
    var form = document.getElementById("intake-form");
    var phoneInput = document.getElementById("phone");
    var photosInput = document.getElementById("photos");

    phoneInput.addEventListener("input", function () {
      var caret = phoneInput.selectionStart;
      var before = phoneInput.value;
      var formatted = formatPhoneAsTyped(before);
      phoneInput.value = formatted;
      // Best-effort caret preservation
      try {
        var diff = formatted.length - before.length;
        phoneInput.setSelectionRange(caret + diff, caret + diff);
      } catch (_) {}
    });

    photosInput.addEventListener("change", function () {
      validatePhotos(photosInput.files);
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      handleSubmit(form, params, config);
    });

    // Clear field errors as user types
    ["first_name", "last_name", "phone", "email", "address", "damage_type"].forEach(function (id) {
      var el = document.getElementById(id);
      var evt = el.tagName === "SELECT" ? "change" : "input";
      el.addEventListener(evt, function () { clearFieldError(id); });
    });
  }

  // ---------- Phone formatting ----------
  function digitsOnly(s) { return (s || "").replace(/\D+/g, ""); }

  function formatPhoneAsTyped(raw) {
    var d = digitsOnly(raw);
    // Drop leading 1 (US country code) for display
    if (d.length === 11 && d[0] === "1") d = d.slice(1);
    d = d.slice(0, 10);
    if (d.length === 0) return "";
    if (d.length < 4) return "(" + d;
    if (d.length < 7) return "(" + d.slice(0, 3) + ") " + d.slice(3);
    return "(" + d.slice(0, 3) + ") " + d.slice(3, 6) + "-" + d.slice(6);
  }

  function normalizePhoneE164(raw) {
    var d = digitsOnly(raw);
    if (d.length === 11 && d[0] === "1") return "+" + d;
    if (d.length === 10) return "+1" + d;
    return null;
  }

  // ---------- Photos ----------
  function validatePhotos(files) {
    if (!files || !files.length) {
      clearFieldError("photos");
      return { ok: true, count: 0 };
    }
    if (files.length > MAX_PHOTOS) {
      setFieldError("photos", "Please select up to " + MAX_PHOTOS + " photos.");
      return { ok: false, count: files.length };
    }
    for (var i = 0; i < files.length; i++) {
      var f = files[i];
      if (ALLOWED_PHOTO_TYPES.indexOf(f.type) < 0) {
        setFieldError("photos", "Photos must be JPG or PNG.");
        return { ok: false, count: files.length };
      }
      if (f.size > MAX_PHOTO_BYTES) {
        setFieldError("photos", "Each photo must be 5MB or less.");
        return { ok: false, count: files.length };
      }
    }
    clearFieldError("photos");
    return { ok: true, count: files.length };
  }

  // ---------- Validation helpers ----------
  function setFieldError(id, message) {
    var input = document.getElementById(id);
    var err = document.getElementById(id + "-error");
    if (input) input.setAttribute("aria-invalid", "true");
    if (err) {
      err.textContent = message;
      err.hidden = false;
    }
  }

  function clearFieldError(id) {
    var input = document.getElementById(id);
    var err = document.getElementById(id + "-error");
    if (input) input.removeAttribute("aria-invalid");
    if (err) {
      err.textContent = "";
      err.hidden = true;
    }
  }

  function setFormError(message, clientPhone) {
    var box = document.getElementById("form-error");
    if (!box) return;
    if (message) {
      box.textContent = message;
      if (clientPhone) box.textContent += " Or call " + clientPhone + ".";
      box.hidden = false;
    } else {
      box.textContent = "";
      box.hidden = true;
    }
  }

  function validate(form) {
    var ok = true;
    function field(id) { return document.getElementById(id); }

    var fn = field("first_name").value.trim();
    if (fn.length < 2) { setFieldError("first_name", "Please enter your first name."); ok = false; }

    var ln = field("last_name").value.trim();
    if (ln.length < 2) { setFieldError("last_name", "Please enter your last name."); ok = false; }

    var phoneE164 = normalizePhoneE164(field("phone").value);
    if (!phoneE164) { setFieldError("phone", "Please enter a valid 10-digit US phone number."); ok = false; }

    var email = field("email").value.trim();
    if (email && !EMAIL_RE.test(email)) { setFieldError("email", "Please enter a valid email or leave it blank."); ok = false; }

    var address = field("address").value.trim();
    if (!address) { setFieldError("address", "Please enter your property address."); ok = false; }

    var damage = field("damage_type").value;
    if (!damage) { setFieldError("damage_type", "Please pick a damage type."); ok = false; }

    var photos = validatePhotos(field("photos").files);
    if (!photos.ok) ok = false;

    return {
      ok: ok,
      values: {
        first_name: fn,
        last_name: ln,
        phone: phoneE164,
        email: email || null,
        address: address,
        damage_type: damage,
        photo_count: photos.count,
        sms_consent: field("sms_consent").checked,
      },
    };
  }

  // ---------- Submit ----------
  function handleSubmit(form, params, config) {
    setFormError("");

    var result = validate(form);
    if (!result.ok) {
      // Move focus to first invalid field for accessibility
      var firstInvalid = form.querySelector('[aria-invalid="true"]');
      if (firstInvalid) firstInvalid.focus();
      return;
    }

    var payload = {
      client_id: params.client_id,
      source: params.source,
      first_name: result.values.first_name,
      last_name: result.values.last_name,
      phone: result.values.phone,
      email: result.values.email,
      address: result.values.address,
      damage_type: result.values.damage_type,
      photo_count: result.values.photo_count,
      sms_consent: result.values.sms_consent,
      sms_consent_at: new Date().toISOString(),
      submitted_at: new Date().toISOString(),
    };

    var button = document.getElementById("submit");
    setLoading(button, true);

    submitPayload(payload, config)
      .then(function () {
        showSuccess(result.values.first_name, result.values.phone, result.values.sms_consent);
      })
      .catch(function (err) {
        console.error("[stormbook-intake] submit failed", err);
        setLoading(button, false);
        var msg = "Something went wrong submitting your request. Please try again.";
        if (err && err.name === "AbortError") {
          msg = "The request timed out. Please check your connection and try again.";
        }
        setFormError(msg, params.phone);
      });
  }

  function submitPayload(payload, config) {
    if (config.mock) {
      console.log("[stormbook-intake] MOCK submit — payload:", payload);
      return new Promise(function (resolve) { setTimeout(resolve, 400); });
    }

    var url = (config.apiBaseUrl || DEFAULTS.apiBaseUrl).replace(/\/+$/, "") + "/intake";
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, SUBMIT_TIMEOUT_MS);

    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    }).then(function (res) {
      clearTimeout(timer);
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json().catch(function () { return {}; });
    }, function (err) {
      clearTimeout(timer);
      throw err;
    });
  }

  function setLoading(button, loading) {
    if (loading) {
      button.classList.add("is-loading");
      button.disabled = true;
      button.setAttribute("aria-busy", "true");
    } else {
      button.classList.remove("is-loading");
      button.disabled = false;
      button.removeAttribute("aria-busy");
    }
  }

  function showSuccess(firstName, phoneE164, smsConsent) {
    document.getElementById("form-view").hidden = true;
    var body = document.getElementById("success-body");
    var headline = document.getElementById("success-headline");
    headline.textContent = "Got it, " + firstName + "!";
    // No SMS goes out without consent, so don't promise one.
    body.textContent = smsConsent
      ? "We'll text you within 60 seconds at " +
        formatPhoneForDisplay(phoneE164) +
        ". Reply to confirm you're the homeowner."
      : "We'll call you at " +
        formatPhoneForDisplay(phoneE164) +
        " to schedule your inspection.";
    var success = document.getElementById("success-view");
    success.hidden = false;
    success.scrollIntoView({ behavior: "instant", block: "start" });
    success.focus && success.focus();
  }

  function formatPhoneForDisplay(e164) {
    var d = digitsOnly(e164);
    if (d.length === 11 && d[0] === "1") d = d.slice(1);
    if (d.length !== 10) return e164;
    return "(" + d.slice(0, 3) + ") " + d.slice(3, 6) + "-" + d.slice(6);
  }
})();
