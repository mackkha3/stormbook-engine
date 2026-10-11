(function () {
  "use strict";

  // ---------- Config ----------
  var DEFAULTS = {
    relayUrl: "http://localhost:3001",
  };
  var SUBMIT_TIMEOUT_MS = 20000;

  var DAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
  ];

  // 7:00 AM → 7:00 PM in 30-minute steps. Wide enough for any demo, short
  // enough to stay a one-tap dropdown.
  var TIMES = buildTimes();

  // Pre-populated so a demo can start with a single tap.
  var PRESETS = {
    contractor_first_name: "Mike",
    contractor_company: "Henderson Roofing",
    homeowner_first_name: "Jane",
    homeowner_phone: "",
    homeowner_address: "123 Elm St",
    damage_type: "Hail",
    slot_a_day: "Tuesday",
    slot_a_time: "10:00 AM",
    slot_b_day: "Thursday",
    slot_b_time: "2:00 PM",
  };

  // Read once at boot from ?t=… — the bookmark on the operator's iPad carries
  // it. Never rendered into the DOM: a screen share is the whole use case.
  var ACCESS_TOKEN = readToken();

  var TEXT_FIELDS = [
    "contractor_first_name",
    "contractor_company",
    "homeowner_first_name",
    "homeowner_phone",
    "homeowner_address",
  ];

  // ---------- Boot ----------
  function readToken() {
    try {
      var t = new URLSearchParams(location.search).get("t");
      return t ? t.trim() : "";
    } catch (_) {
      return "";
    }
  }

  function showTokenWarning() {
    if (ACCESS_TOKEN) return;
    var warn = document.getElementById("token-warning");
    if (warn) warn.hidden = false;
  }

  document.addEventListener("DOMContentLoaded", function () {
    populateSelects();
    applyPresets();
    showTokenWarning();
    loadConfig().then(start).catch(function (err) {
      console.error("[stormbook-demo] config load failed", err);
      start(DEFAULTS);
    });
  });

  function loadConfig() {
    if (location.protocol === "file:") return Promise.resolve(DEFAULTS);
    return fetch("config.json", { cache: "no-cache" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (live) {
        if (live) return merge(DEFAULTS, live);
        return fetch("config.example.json", { cache: "no-cache" })
          .then(function (r) { return r.ok ? r.json() : DEFAULTS; })
          .catch(function () { return DEFAULTS; })
          .then(function (ex) { return merge(DEFAULTS, ex); });
      });
  }

  function merge(base, override) {
    var out = {};
    for (var k in base) out[k] = base[k];
    if (override && typeof override === "object") {
      for (var k2 in override) if (override[k2] !== undefined) out[k2] = override[k2];
    }
    return out;
  }

  function start(config) {
    var relay = (config.relayUrl || DEFAULTS.relayUrl).replace(/\/+$/, "");
    document.getElementById("relay-target").textContent = "relay: " + relay;
    wireForm(relay);
  }

  // ---------- Option building ----------
  function buildTimes() {
    var out = [];
    for (var h = 7; h <= 19; h++) {
      var meridiem = h < 12 ? "AM" : "PM";
      var display = h % 12 === 0 ? 12 : h % 12;
      out.push(display + ":00 " + meridiem);
      if (h !== 19) out.push(display + ":30 " + meridiem);
    }
    return out;
  }

  function fill(selectId, values) {
    var select = document.getElementById(selectId);
    if (!select) return;
    select.innerHTML = "";
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  }

  function populateSelects() {
    fill("slot_a_day", DAYS);
    fill("slot_b_day", DAYS);
    fill("slot_a_time", TIMES);
    fill("slot_b_time", TIMES);
  }

  function applyPresets() {
    Object.keys(PRESETS).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.value = PRESETS[id];
    });
  }

  // ---------- Form wiring ----------
  function wireForm(relay) {
    var form = document.getElementById("demo-form");
    var phoneInput = document.getElementById("homeowner_phone");

    phoneInput.addEventListener("input", function () {
      var caret = phoneInput.selectionStart;
      var before = phoneInput.value;
      var formatted = formatPhoneAsTyped(before);
      phoneInput.value = formatted;
      try {
        var diff = formatted.length - before.length;
        phoneInput.setSelectionRange(caret + diff, caret + diff);
      } catch (_) {}
    });

    TEXT_FIELDS.forEach(function (id) {
      document.getElementById(id).addEventListener("input", function () {
        clearFieldError(id);
      });
    });

    document.getElementById("sms_consent").addEventListener("change", function () {
      clearFieldError("sms_consent");
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      handleSubmit(relay);
    });

    document.getElementById("fire-another").addEventListener("click", function () {
      // Form state is intact underneath — going back is instant mid-call.
      document.getElementById("sent-view").hidden = true;
      document.getElementById("form-view").hidden = false;
      // Consent is per send — it never carries over to the next fire.
      document.getElementById("sms_consent").checked = false;
      document.getElementById("homeowner_phone").focus();
    });

    phoneInput.focus();
  }

  // ---------- Phone ----------
  function digitsOnly(s) { return (s || "").replace(/\D+/g, ""); }

  function formatPhoneAsTyped(raw) {
    var d = digitsOnly(raw);
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

  function formatPhoneForDisplay(e164) {
    var d = digitsOnly(e164);
    if (d.length === 11 && d[0] === "1") d = d.slice(1);
    if (d.length !== 10) return e164;
    return "(" + d.slice(0, 3) + ") " + d.slice(3, 6) + "-" + d.slice(6);
  }

  // ---------- Validation ----------
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

  function setFormError(message) {
    var box = document.getElementById("form-error");
    if (!box) return;
    if (message) {
      box.textContent = message;
      box.hidden = false;
    } else {
      box.textContent = "";
      box.hidden = true;
    }
  }

  function value(id) { return (document.getElementById(id).value || "").trim(); }

  function validate() {
    var ok = true;

    [
      ["contractor_first_name", "Rep first name is required."],
      ["contractor_company", "Company name is required."],
      ["homeowner_first_name", "Homeowner first name is required."],
      ["homeowner_address", "Property address is required."],
    ].forEach(function (pair) {
      if (!value(pair[0])) { setFieldError(pair[0], pair[1]); ok = false; }
    });

    var phone = normalizePhoneE164(value("homeowner_phone"));
    if (!phone) {
      setFieldError("homeowner_phone", "Enter a valid 10-digit US phone number.");
      ok = false;
    }

    var slotsError = document.getElementById("slots-error");
    slotsError.hidden = true;
    if (value("slot_a_day") === value("slot_b_day")) {
      // Same day on both slots makes the reply ambiguous — the relay would
      // classify it UNMATCHED and the demo would stall on a dead beat.
      slotsError.textContent = "Slot A and Slot B must be different days.";
      slotsError.hidden = false;
      ok = false;
    }

    // Never pre-checked, never required by the browser — but the relay only
    // texts with consent, so an unchecked box stops here instead of firing.
    var consent = document.getElementById("sms_consent").checked;
    if (!consent) {
      setFieldError("sms_consent", "The demo text requires SMS consent.");
      ok = false;
    }

    return {
      ok: ok,
      payload: {
        contractor_first_name: value("contractor_first_name"),
        contractor_company: value("contractor_company"),
        homeowner_first_name: value("homeowner_first_name"),
        homeowner_phone: phone,
        homeowner_address: value("homeowner_address"),
        damage_type: value("damage_type"),
        slot_a_day: value("slot_a_day"),
        slot_a_time: value("slot_a_time"),
        slot_b_day: value("slot_b_day"),
        slot_b_time: value("slot_b_time"),
        sms_consent: consent,
        sms_consent_at: new Date().toISOString(),
      },
    };
  }

  // ---------- Submit ----------
  function handleSubmit(relay) {
    setFormError("");

    var result = validate();
    if (!result.ok) {
      var firstInvalid = document.querySelector('[aria-invalid="true"]');
      if (firstInvalid) firstInvalid.focus();
      return;
    }

    var button = document.getElementById("submit");
    setLoading(button, true);

    postFire(relay, result.payload)
      .then(function (body) {
        setLoading(button, false);
        showSent(result.payload, body);
      })
      .catch(function (err) {
        console.error("[stormbook-demo] fire failed", err);
        setLoading(button, false);
        var msg = "Relay error: " + (err && err.message ? err.message : "unknown");
        if (err && err.name === "AbortError") {
          msg = "The relay timed out. Check that it's awake, then fire again.";
        } else if (err && err.name === "AccessDeniedError") {
          msg = err.message;
        }
        // Form state is untouched — no retyping mid-call.
        setFormError(msg);
      });
  }

  function postFire(relay, payload) {
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, SUBMIT_TIMEOUT_MS);

    var headers = { "Content-Type": "application/json", "Accept": "application/json" };
    if (ACCESS_TOKEN) headers["Authorization"] = "Bearer " + ACCESS_TOKEN;

    return fetch(relay + "/fire", {
      method: "POST",
      headers: headers,
      body: JSON.stringify(payload),
      signal: controller.signal,
    }).then(function (res) {
      clearTimeout(timer);
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (!res.ok) {
          if (res.status === 401) {
            // Deliberately says nothing about the token's value.
            var denied = new Error(
              "Access denied — token missing or invalid. Check the URL."
            );
            denied.name = "AccessDeniedError";
            throw denied;
          }
          throw new Error(body.detail || body.code || "HTTP " + res.status);
        }
        return body;
      });
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
      button.querySelector(".cta-label").textContent = "Firing SMS…";
    } else {
      button.classList.remove("is-loading");
      button.disabled = false;
      button.removeAttribute("aria-busy");
      button.querySelector(".cta-label").textContent = "Fire SMS";
    }
  }

  function showSent(payload, body) {
    document.getElementById("form-view").hidden = true;

    var stamp = body.message_sent_at
      ? new Date(body.message_sent_at).toLocaleTimeString()
      : new Date().toLocaleTimeString();

    document.getElementById("sent-headline").textContent = "SMS fired at " + stamp;
    document.getElementById("sent-body").textContent =
      "Sent to " + formatPhoneForDisplay(payload.homeowner_phone) + ".";
    document.getElementById("sent-preview").textContent =
      body.message_body || "(relay returned no message body)";

    var sent = document.getElementById("sent-view");
    sent.hidden = false;
    sent.scrollIntoView({ behavior: "instant", block: "start" });
  }
})();
