(() => {
  'use strict';

  const qs = (selector, context = document) => context.querySelector(selector);
  const qsa = (selector, context = document) => [...context.querySelectorAll(selector)];

  const header = qs('.site-header');
  const mobileMenu = qs('.mobile-menu');
  const menuToggle = qs('.menu-toggle');
  const mobileCta = qs('.mobile-cta');
  const bookingSection = qs('#booking');

  const onScroll = () => {
    header?.classList.toggle('scrolled', window.scrollY > 30);
    if (bookingSection && mobileCta) {
      const rect = bookingSection.getBoundingClientRect();
      mobileCta.classList.toggle('prehide', window.scrollY < 620);
      mobileCta.classList.toggle('hide', rect.top < window.innerHeight * 0.75);
    }
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  menuToggle?.addEventListener('click', () => {
    const open = !mobileMenu.classList.contains('open');
    mobileMenu.classList.toggle('open', open);
    mobileMenu.setAttribute('aria-hidden', String(!open));
    menuToggle.setAttribute('aria-expanded', String(open));
  });
  qsa('.mobile-menu a').forEach((link) => link.addEventListener('click', () => {
    mobileMenu.classList.remove('open');
    mobileMenu.setAttribute('aria-hidden', 'true');
    menuToggle?.setAttribute('aria-expanded', 'false');
  }));

  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const delay = Number(entry.target.dataset.delay || 0);
      window.setTimeout(() => entry.target.classList.add('is-visible'), delay);
      revealObserver.unobserve(entry.target);
    });
  }, { threshold: 0.12 });
  qsa('.reveal').forEach((element) => revealObserver.observe(element));

  const countObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting || entry.target.dataset.counted) return;
      entry.target.dataset.counted = 'true';
      const target = Number(entry.target.dataset.count || 0);
      const start = performance.now();
      const duration = 1100;
      const tick = (time) => {
        const progress = Math.min(1, (time - start) / duration);
        const eased = 1 - Math.pow(1 - progress, 3);
        entry.target.textContent = String(Math.round(target * eased));
        if (progress < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  }, { threshold: .4 });
  qsa('[data-count]').forEach((element) => countObserver.observe(element));

  const glow = qs('.cursor-glow');
  if (glow && window.matchMedia('(pointer:fine)').matches) {
    window.addEventListener('pointermove', (event) => {
      glow.style.left = `${event.clientX}px`;
      glow.style.top = `${event.clientY}px`;
      glow.style.opacity = '1';
    }, { passive: true });
  }

  qsa('.magnetic').forEach((element) => {
    if (!window.matchMedia('(pointer:fine)').matches) return;
    element.addEventListener('mousemove', (event) => {
      const rect = element.getBoundingClientRect();
      const x = event.clientX - rect.left - rect.width / 2;
      const y = event.clientY - rect.top - rect.height / 2;
      element.style.transform = `translate(${x * .08}px, ${y * .12}px)`;
    });
    element.addEventListener('mouseleave', () => { element.style.transform = ''; });
  });

  const ranges = {
    leads: qs('#leadsInput'),
    afterHours: qs('#afterHoursInput'),
    closeRate: qs('#closeRateInput'),
    jobValue: qs('#jobValueInput')
  };

  const outputs = {
    leads: qs('#leadsOutput'),
    afterHours: qs('#afterHoursOutput'),
    closeRate: qs('#closeRateOutput'),
    jobValue: qs('#jobValueOutput'),
    afterHoursLeads: qs('#afterHoursLeads'),
    lostLeads: qs('#lostLeads'),
    lostJobs: qs('#lostJobs'),
    revenueLeak: qs('#revenueLeak'),
    halfLeak: qs('#halfLeak')
  };

  const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

  const setRangeProgress = (input) => {
    const min = Number(input.min || 0);
    const max = Number(input.max || 100);
    const value = Number(input.value);
    const percent = ((value - min) / (max - min)) * 100;
    input.style.setProperty('--range-progress', `${percent}%`);
  };

  const updateCalculator = () => {
    if (!ranges.leads) return;
    const leads = Number(ranges.leads.value);
    const afterHoursShare = Number(ranges.afterHours.value) / 100;
    const closeRate = Number(ranges.closeRate.value) / 100;
    const jobValue = Number(ranges.jobValue.value);
    const afterHoursLeads = Math.round(leads * afterHoursShare);
    const lostLeads = Math.round(afterHoursLeads * .5);
    const lostJobs = Math.round(lostLeads * closeRate * 10) / 10;
    const leak = Math.round(lostJobs * jobValue);

    outputs.leads.textContent = String(leads);
    outputs.afterHours.textContent = `${Math.round(afterHoursShare * 100)}%`;
    outputs.closeRate.textContent = `${Math.round(closeRate * 100)}%`;
    outputs.jobValue.textContent = money.format(jobValue);
    outputs.afterHoursLeads.textContent = String(afterHoursLeads);
    outputs.lostLeads.textContent = String(lostLeads);
    outputs.lostJobs.textContent = Number.isInteger(lostJobs) ? String(lostJobs) : lostJobs.toFixed(1);
    outputs.revenueLeak.textContent = money.format(leak);
    outputs.halfLeak.textContent = `${money.format(leak / 2)} / month`;
    Object.values(ranges).forEach(setRangeProgress);
  };

  Object.values(ranges).forEach((range) => range?.addEventListener('input', updateCalculator));
  updateCalculator();

  const modal = qs('.calendar-modal');
  const modalScroll = qs('.modal-form-scroll');
  const embeddedForm = qs('#inline-i6myyENcn8kq10E3Xd6R');
  const embeddedFormContainer = qs('.ghl-form-container');
  let modalIsOpen = false;
  let lockedScrollY = 0;

  const markEmbeddedFormLoaded = () => {
    embeddedFormContainer?.classList.add('is-loaded');
  };

  // The iframe is preloaded with a normal src so it is ready before a visitor
  // opens the modal. Never focus it automatically: doing so opens the iPhone
  // keyboard before the form is visible and can leave Safari stuck.
  embeddedForm?.addEventListener('load', markEmbeddedFormLoaded);
  window.setTimeout(() => {
    // Fail-safe: show the iframe even if a browser serves it from cache before
    // the load listener attaches.
    embeddedFormContainer?.classList.add('is-loaded');
  }, 5000);

  const lockPageScroll = () => {
    lockedScrollY = window.scrollY || window.pageYOffset || 0;
    document.body.style.position = 'fixed';
    document.body.style.top = `-${lockedScrollY}px`;
    document.body.style.left = '0';
    document.body.style.right = '0';
    document.body.style.width = '100%';
    document.body.classList.add('modal-open');
  };

  const unlockPageScroll = () => {
    document.body.classList.remove('modal-open');
    document.body.style.position = '';
    document.body.style.top = '';
    document.body.style.left = '';
    document.body.style.right = '';
    document.body.style.width = '';
    window.scrollTo(0, lockedScrollY);
  };

  const openAuditForm = () => {
    if (!modal || modalIsOpen) return;
    mobileMenu?.classList.remove('open');
    mobileMenu?.setAttribute('aria-hidden', 'true');
    menuToggle?.setAttribute('aria-expanded', 'false');
    modalIsOpen = true;
    lockPageScroll();
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    modalScroll?.scrollTo({ top: 0, behavior: 'auto' });
  };

  const closeAuditForm = () => {
    if (!modal || !modalIsOpen) return;
    modalIsOpen = false;

    // If a form field inside the cross-origin iframe has focus, blurring the
    // iframe dismisses the mobile keyboard before the overlay is removed.
    if (document.activeElement === embeddedForm) embeddedForm.blur();

    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    unlockPageScroll();
  };

  // Capture-phase delegation makes every current and future audit trigger
  // reliable, including tier links and the founding-cohort link. It also runs
  // before any third-party click handler can cancel the event.
  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('.calendar-trigger, .calendar-launch');
    if (!trigger) return;
    event.preventDefault();
    openAuditForm();
  }, true);

  document.addEventListener('click', (event) => {
    if (!event.target.closest('[data-close-modal]')) return;
    event.preventDefault();
    event.stopPropagation();
    closeAuditForm();
  }, true);

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && modalIsOpen) closeAuditForm();
  });

  qsa('a[aria-disabled="true"]').forEach((link) => link.addEventListener('click', (event) => event.preventDefault()));
})();
