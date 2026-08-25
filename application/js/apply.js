(() => {
  const TOTAL = 10;
  const form = document.getElementById("apply-form");
  const done = document.getElementById("apply-done");
  const errorEl = document.getElementById("form-error");
  const nextBtn = document.getElementById("next-btn");
  const backBtn = document.getElementById("back-btn");
  const stateName = document.getElementById("apply-state");
  const stateSelect = document.getElementById("primary-state");
  const progressLabel = document.getElementById("progress-label");
  const progressBar = document.getElementById("progress-bar");
  const progressFill = document.getElementById("progress-fill");
  const progress = document.querySelector(".progress");
  const steps = Array.from(form.querySelectorAll(".step"));

  let current = 1;

  const emailOk = (value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
  const digits = (value) => value.replace(/\D/g, "");

  const showError = (message) => {
    errorEl.hidden = !message;
    errorEl.textContent = message || "";
  };

  const currentStep = () => form.querySelector(`.step[data-step="${current}"]`);

  const updateProgress = () => {
    progressLabel.textContent = `Step ${current} of ${TOTAL}`;
    progressBar.setAttribute("aria-valuenow", String(current));
    progressFill.style.width = `${(current / TOTAL) * 100}%`;
    backBtn.hidden = current === 1;
    nextBtn.textContent = current === TOTAL ? "Pick a time →" : "Continue →";
  };

  const showStep = (n) => {
    current = n;
    steps.forEach((step) => {
      const active = Number(step.dataset.step) === n;
      step.hidden = !active;
      step.classList.toggle("is-active", active);
    });
    updateProgress();
    showError("");
    const heading = currentStep().querySelector("legend");
    if (heading) heading.setAttribute("tabindex", "-1");
    if (heading && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      heading.focus({ preventScroll: true });
    }
  };

  const validate = () => {
    const data = new FormData(form);

    if (current === 1 && !data.get("years")) {
      return "Select how many years you have been in commercial roofing.";
    }
    if (current === 2 && !data.get("lastYearRev")) {
      return "Select last year’s revenue.";
    }
    if (current === 3 && !data.get("thisYearGoal")) {
      return "Select this year’s revenue goal.";
    }
    if (current === 4 && !stateSelect.value) {
      return "Select your primary state.";
    }
    if (current === 5 && form.querySelectorAll('input[name="specialties"]:checked').length === 0) {
      return "Select at least one specialty.";
    }
    if (current === 6 && !String(data.get("company") || "").trim()) {
      return "Enter your company name.";
    }
    if (current === 7 && !String(data.get("website") || "").trim()) {
      return "Enter your website, or type none.";
    }
    if (current === 8 && !String(data.get("fullName") || "").trim()) {
      return "Enter your full name.";
    }
    if (current === 9 && !emailOk(String(data.get("email") || "").trim())) {
      return "Enter a valid email address.";
    }
    if (current === 10) {
      if (digits(String(data.get("phone") || "")).length < 10) {
        return "Enter a phone number with at least 10 digits.";
      }
      if (!form.querySelector("#consent").checked) {
        return "Consent is required so we can call or text you about this application.";
      }
    }
    return "";
  };

  const finish = () => {
    form.hidden = true;
    if (progress) progress.hidden = true;
    done.hidden = false;
    done.setAttribute("tabindex", "-1");
    done.focus({ preventScroll: true });
  };

  stateSelect.addEventListener("change", () => {
    stateName.textContent = stateSelect.value || "California";
  });

  backBtn.addEventListener("click", () => {
    if (current > 1) showStep(current - 1);
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const message = validate();
    if (message) {
      showError(message);
      return;
    }
    if (current < TOTAL) {
      showStep(current + 1);
      return;
    }
    finish();
  });

  const withAutoplay = (url) => {
    const parsed = new URL(url, window.location.href);
    parsed.searchParams.set("autoplay", "1");
    return parsed.toString();
  };

  document.querySelectorAll("img[data-fallback]").forEach((img) => {
    img.addEventListener("error", () => {
      if (img.dataset.fallback && img.src !== img.dataset.fallback) {
        img.src = img.dataset.fallback;
      }
    });
  });

  const logo = document.getElementById("logo-img");
  const wordmark = document.querySelector(".wordmark");
  if (logo && wordmark) {
    logo.addEventListener("error", () => {
      logo.hidden = true;
      wordmark.hidden = false;
    });
  }

  document.querySelectorAll(".video-card[data-embed]").forEach((card) => {
    card.addEventListener("click", () => {
      if (card.classList.contains("is-playing")) return;
      const iframe = document.createElement("iframe");
      iframe.src = withAutoplay(card.dataset.embed);
      iframe.title = card.dataset.title || "Client testimonial";
      iframe.allow =
        "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share";
      iframe.allowFullscreen = true;
      iframe.loading = "lazy";
      card.classList.add("is-playing");
      card.replaceChildren(iframe);
    });
  });

  document.querySelectorAll('a[href="#apply"]').forEach((link) => {
    link.addEventListener("click", (event) => {
      const target = document.getElementById("apply");
      if (!target) return;
      event.preventDefault();
      history.pushState(null, "", "#apply");
      target.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "auto"
          : "smooth",
        block: "start",
      });
    });
  });

  updateProgress();
})();
