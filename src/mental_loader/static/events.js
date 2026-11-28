// Direct events controls share the browser session and operation results with chat.
const eventActions = {
  gift: 'Gift', call: 'Call or message', card: 'Birthday card', meal: 'Plan a meal',
  flowers: 'Flowers', experience: 'An experience', delivery: 'Post or deliver', cake: 'Cake or treats',
};
let eventBoard = null;
let eventTasks = [];
let eventBirthdays = [];
let calendarMonth = null;
let calendarDay = null;
let agenda = false;
let peopleDraft = null;
const previewRequests = new Map();
const dismissedPreviews = new Set();

function scopedBody(fields = {}) {
  return JSON.stringify({conversation_id: conversation, ...fields});
}

function element(tag, content, className) {
  return textElement(tag, content, className);
}

function actionButton(label, callback, className = 'quiet') {
  const button = element('button', label, className);
  button.type = 'button';
  button.onclick = callback;
  return button;
}

function inHouseholdDay(timestamp = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: me?.timezone || 'UTC', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date(timestamp));
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return [values.year, values.month, values.day].join('-');
}

function dateLabel(day, options) {
  return new Date(day + 'T12:00:00Z').toLocaleDateString([], {...options, timeZone: 'UTC'});
}

function calendarEntries() {
  const birthdays = eventBirthdays.filter(row =>
    row.conversation_id === conversation && row.gift_status !== 'cancelled'
  ).map(row => ({day: row.occasion_date, title: row.recipient + '’s birthday', birthday: row.id}));
  const tasks = eventTasks.filter(task =>
    task.status === 'open' && task.conversation_id === conversation
  ).map(task => ({day: inHouseholdDay(task.due_at), title: task.title, task: task.id}));
  return [...birthdays, ...tasks].sort((a, b) => a.day.localeCompare(b.day));
}

function showCalendarEntries(list, entries) {
  list.replaceChildren();
  if (!entries.length) list.append(element('p', 'Nothing planned here.', 'fine'));
  entries.forEach(item => {
    const row = element('div', '', 'calendar-entry');
    if (item.task) {
      row.append(actionButton('✓', async () => {
        try {
          await api('/api/tasks/' + item.task + '/complete', {method: 'POST'});
          await refresh();
        } catch (error) { notice(error.message); }
      }));
      row.lastChild.setAttribute('aria-label', 'Complete ' + item.title);
    }
    if (item.birthday) {
      row.append(actionButton(item.title, () => {
        const card = document.getElementById('birthday-' + item.birthday);
        const plan = card?.querySelector('details');
        if (plan) plan.open = true;
        card?.scrollIntoView({block: 'nearest'});
      }));
    } else row.append(element('span', item.title));
    list.append(row);
  });
}

function renderCalendar() {
  if (!me) return;
  calendarMonth ||= inHouseholdDay().slice(0, 7);
  const firstDay = calendarMonth + '-01';
  const first = new Date(firstDay + 'T12:00:00Z');
  const grid = $('#calendar-grid');
  const selected = $('#calendar-day');
  const entries = calendarEntries();
  $('#calendar-month').textContent = dateLabel(firstDay, {month: 'long', year: 'numeric'});
  $('#calendar-view').textContent = agenda ? 'Calendar' : 'Agenda';
  grid.hidden = agenda;
  grid.replaceChildren();
  if (agenda) {
    const monthEntries = entries.filter(item => item.day.startsWith(calendarMonth));
    selected.replaceChildren();
    if (!monthEntries.length) selected.append(element('p', 'Nothing planned this month.', 'fine'));
    for (const day of new Set(monthEntries.map(item => item.day))) {
      const group = element('div', '', 'agenda-group');
      showCalendarEntries(group, monthEntries.filter(item => item.day === day));
      group.prepend(element('strong', dateLabel(day, {weekday: 'short', day: 'numeric', month: 'short'})));
      selected.append(group);
    }
    return;
  }
  ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].forEach(label =>
    grid.append(element('span', label, 'weekday'))
  );
  const start = (first.getUTCDay() + 6) % 7;
  const length = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  const cells = Math.ceil((start + length) / 7) * 7;
  for (let index = 0; index < cells; index++) {
    const date = new Date(first);
    date.setUTCDate(index - start + 1);
    const day = date.toISOString().slice(0, 10);
    const dayEvents = entries.filter(item => item.day === day);
    const button = actionButton(String(date.getUTCDate()), () => {
      calendarDay = day;
      renderCalendar();
      grid.querySelector('[aria-pressed="true"]')?.focus();
    }, 'calendar-cell');
    button.classList.toggle('other-month', !day.startsWith(calendarMonth));
    button.setAttribute('aria-pressed', String(day === calendarDay));
    button.setAttribute('aria-label', dateLabel(day, {month: 'long', day: 'numeric'}) +
      (dayEvents.length ? ', ' + dayEvents.map(item => item.title).join(', ') : ''));
    if (dayEvents.length) {
      const marker = element('span', dayEvents.some(item => item.birthday) ? '●' : '▪', 'calendar-mark');
      marker.setAttribute('aria-hidden', 'true');
      button.append(marker);
      button.append(element('span', dayEvents[0].title, 'calendar-cell-title'));
    }
    grid.append(button);
  }
  const day = calendarDay || (inHouseholdDay().startsWith(calendarMonth) ? inHouseholdDay() : firstDay);
  showCalendarEntries(selected, entries.filter(item => item.day === day));
  selected.prepend(element('strong', dateLabel(day, {weekday: 'long', day: 'numeric', month: 'long'})));
}

function shiftCalendar(months) {
  const month = new Date((calendarMonth || inHouseholdDay().slice(0, 7)) + '-01T12:00:00Z');
  month.setUTCMonth(month.getUTCMonth() + months);
  calendarMonth = month.toISOString().slice(0, 7);
  calendarDay = null;
  renderCalendar();
}

let birthdayReview = null;

function updateBirthdaySelectionCount() {
  const picked = $('#birthday-sync-people').querySelectorAll('input:checked').length;
  $('#birthday-selection-count').textContent = picked + ' selected';
}

async function openBirthdayPicker() {
  const scope = conversation;
  const button = $('#birthday-sync-toggle');
  button.disabled = true;
  $('#birthday-sync-picker').hidden = false;
  $('#birthday-selection-error').textContent = 'Reading birthday names and dates…';
  $('#birthday-save-selection').disabled = true;
  $('#birthday-sync-people').querySelectorAll('label').forEach(label => label.remove());
  birthdayReview = null;
  try {
    const review = await api('/api/events/sync/people', {method: 'POST', body: scopedBody()});
    if (scope !== conversation || $('#birthday-sync-picker').hidden) return;
    birthdayReview = review;
    const list = $('#birthday-sync-people');
    review.people.forEach((person, index) => {
      const label = element('label', '', 'sync-person');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = String(index);
      checkbox.checked = person.selected;
      checkbox.onchange = updateBirthdaySelectionCount;
      const description = element('span', person.name);
      if (person.date) description.append(element('small',
        dateLabel(person.date, {day: 'numeric', month: 'long'}) + (person.new ? ' · New' : ''), 'fine'));
      label.append(checkbox, description);
      list.append(label);
    });
    updateBirthdaySelectionCount();
    $('#birthday-selection-error').textContent = review.people.length ? '' : 'No upcoming birthdays were found.';
    $('#birthday-save-selection').disabled = false;
  } catch (error) {
    if (scope === conversation) $('#birthday-selection-error').textContent = error.message;
  } finally { button.disabled = false; }
}

function renderSync(data, privateChat) {
  const owner = me?.role === 'owner' && !privateChat && data.can_manage !== false;
  const descriptions = {
    off: privateChat ? 'Calendar sync is available in shared chats.' :
      'Connect Google Calendar in Settings, then choose whose birthdays to sync.',
    review: 'Choose whose birthdays to sync before weekly refreshes begin.',
    waiting: 'Your selection is saved. Waiting to resync.',
    syncing: 'Checking Google Calendar…',
    synced: data.last_success ? 'Last synced ' + new Date(data.last_success).toLocaleString() : 'Synced',
    unavailable: 'Calendar unavailable. Check your Google connection in Settings.',
  };
  const status = descriptions[data.state] || descriptions.off;
  $('#birthday-sync-status').textContent = status +
    (data.enabled ? ' · ' + (data.selected_count || 0) + ' selected · Weekly resync' : '');
  const toggle = $('#birthday-sync-toggle');
  toggle.hidden = !owner;
  toggle.textContent = data.new_count ? 'Review ' + data.new_count + ' new' :
    data.enabled ? 'Edit synced people' : 'Choose birthdays';
  toggle.onclick = openBirthdayPicker;
  $('#birthday-sync-now').hidden = !owner || !data.enabled;
  $('#birthday-pause-sync').hidden = !owner || !data.enabled;
  $('#assistant-calendar-review').hidden = !owner || !data.new_count;
  $('#assistant-calendar-copy').textContent = data.new_count === 1 ?
    'I found one new birthday in Google Calendar. Would you like to include it?' :
    'I found ' + (data.new_count || 0) + ' new birthdays in Google Calendar. Would you like to include any?';
  const assistant = $('#assistant-toggle');
  if (data.new_count) assistant.textContent = '✧ Assistant · New birthdays';
  else if (assistant.textContent.includes('New birthdays')) {
    assistant.textContent = $('#assistant-previews').childElementCount ? '✧ Assistant · Review ready' : '✧ Assistant';
  }
}

function addPlanFields(container, plan, onChange) {
  const fieldset = element('fieldset', '', 'plan-options');
  fieldset.append(element('legend', 'Birthday preparations', 'sr-only'));
  const more = element('details', '', 'more-actions');
  more.append(element('summary', 'More ways to celebrate'));
  const extras = element('div', '', 'plan-options');
  more.append(extras);
  const primary = ['gift', 'call', 'card', 'meal'];
  for (const [key, title] of Object.entries(eventActions)) {
    const label = element('label', '', 'plan-option');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = plan.actions.includes(key);
    input.onchange = () => {
      plan.actions = input.checked ? [...plan.actions, key] : plan.actions.filter(item => item !== key);
      onChange?.();
    };
    label.append(input, document.createTextNode(title));
    (primary.includes(key) ? fieldset : extras).append(label);
  }
  container.append(fieldset, more);
  const fields = element('div', '', 'plan-numbers');
  for (const [key, title, max] of [
    ['budget', 'Gift budget (AUD)', 100000],
    ['lead_days', 'Gift reminder days before', 90],
    ['nudge_days', 'Birthday heads-up days before', 90],
  ]) {
    const label = element('label', title);
    const input = document.createElement('input');
    input.type = 'number';
    input.min = '0';
    input.max = String(max);
    input.required = true;
    input.value = plan[key];
    input.oninput = () => { if (input.checkValidity()) plan[key] = Number(input.value); };
    label.append(input);
    fields.append(label);
  }
  container.append(fields);
}

function birthdayPlanControl(row) {
  const details = element('details', '', 'birthday-plan');
  const labels = row.plan?.actions.map(key => eventActions[key]) || ['Gift'];
  details.append(element('summary', (labels.join(' · ') || 'Heads-up only') + ' · Edit plan'));
  const draft = structuredClone(row.plan || {
    actions: ['gift'], budget: Math.min(100000, Math.round(Number(row.budget ?? 50))),
    lead_days: Math.min(90, row.lead_days), nudge_days: 14,
  });
  delete draft.custom;
  const form = element('form', '', 'record-form');
  addPlanFields(form, draft);
  const save = element('button', 'Save this birthday plan');
  save.type = 'submit';
  form.append(save);
  form.onsubmit = async event => {
    event.preventDefault();
    if (conversation !== row.conversation_id) return;
    save.disabled = true;
    try {
      await api('/api/events/birthday-plan', {method: 'PUT', body: scopedBody({...draft, birthday_id: row.id})});
      await refresh();
      notice('Birthday plan saved.');
    } catch (error) { notice(error.message); }
    finally { save.disabled = false; }
  };
  details.append(form);
  return details;
}

function renderPeopleStep() {
  const draft = peopleDraft;
  const fields = $('#people-fields');
  fields.replaceChildren();
  $('#people-heading').textContent = ['Your usual birthday plan', 'Personalise by person', 'Review your defaults'][draft.step - 1];
  $('#people-step-copy').textContent = [
    'Choose what you usually do. Cards are optional.',
    'People can inherit your usual plan or have their own choices.',
    'Birthdays you edited individually keep their plans.',
  ][draft.step - 1];
  $('#people-back').hidden = draft.step === 1;
  $('#people-update-label').hidden = draft.step !== 3;
  $('#people-next').textContent = ['Personalise by person →', 'Review defaults →', 'Save defaults'][draft.step - 1];
  if (draft.step === 1) {
    addPlanFields(fields, draft.defaults);
    return;
  }
  if (!draft.people.length) fields.append(element('p', 'People appear as birthdays sync. You can also add a person when saving a gift idea.', 'fine'));
  for (const person of draft.people) {
    const block = element('div', '', 'person-default');
    block.append(element('strong', person.name));
    if (draft.step === 2) {
      const inherit = document.createElement('input');
      inherit.type = 'checkbox';
      inherit.checked = !draft.overrides[person.id];
      const label = element('label', '');
      label.append(inherit, document.createTextNode('Use my usual defaults'));
      inherit.onchange = () => {
        draft.overrides[person.id] = inherit.checked ? null : structuredClone(draft.defaults);
        renderPeopleStep();
      };
      block.append(label);
      if (!inherit.checked) addPlanFields(block, draft.overrides[person.id]);
    } else {
      const settings = draft.overrides[person.id] || draft.defaults;
      block.append(element('p', (draft.overrides[person.id] ? 'Personal defaults · ' : 'Usual defaults · ') +
        (settings.actions.map(key => eventActions[key]).join(', ') || 'Heads-up only') +
        (settings.actions.includes('gift') ? ' · AUD ' + settings.budget : ''), 'fine'));
    }
    fields.append(block);
  }
}

function populatePeopleSelect(select, fallback, existing = []) {
  const previous = select.value;
  select.replaceChildren(new Option(fallback, ''), ...existing.map(p => new Option(p.name, p.id)));
  select.value = [...select.options].some(item => item.value === previous) ? previous : '';
}

function renderSavedIdeas() {
  const select = $('#gift-idea-form').elements.person;
  const person = eventBoard?.people.find(item => item.id === select.value);
  $('#new-idea-name').hidden = Boolean(person);
  const list = $('#saved-ideas');
  list.replaceChildren();
  for (const idea of person?.ideas || []) {
    const row = element('div', '', 'calendar-entry');
    row.append(element('span', idea.text), actionButton('Remove', async () => {
      try {
        await api('/api/events/gift-ideas', {
          method: 'DELETE', body: scopedBody({person_id: person.id, idea_id: idea.id}),
        });
        await refresh();
      } catch (error) { notice(error.message); }
    }));
    list.append(row);
  }
}

function renderAssistantPreviews(previews) {
  const target = $('#assistant-previews');
  const pending = previews.filter(item => !dismissedPreviews.has(item.preview_id));
  const signature = JSON.stringify(pending);
  if (target.dataset.signature === signature) return;
  target.dataset.signature = signature;
  target.replaceChildren();
  for (const proposal of pending) {
    const scope = conversation;
    const card = element('details', '', 'gift-preview');
    card.append(element('summary', 'Review ' + proposal.changes.length + ' proposed gift changes'));
    const scopeLabels = {defaults: 'Usual defaults', occasions: 'Upcoming birthdays', both: 'Defaults and upcoming birthdays'};
    card.append(element('p', scopeLabels[proposal.scope], 'fine'));
    for (const change of proposal.changes) {
      const differences = [];
      if (change.before.budget !== change.after.budget) {
        differences.push('AUD ' + change.before.budget + ' → ' + change.after.budget);
      }
      if (change.before.actions.includes('card') !== change.after.actions.includes('card')) {
        differences.push(change.after.actions.includes('card') ? 'Add a card' : 'Remove card');
      }
      card.append(element('p', change.name + (change.date ? ' · ' + change.date : ' · usual defaults') +
        ' · ' + (differences.join(', ') || 'No change'), 'fine'));
    }
    const apply = actionButton('Apply these changes', async () => {
      if (scope !== conversation) return;
      apply.disabled = true;
      const id = proposal.preview_id;
      if (!previewRequests.has(id)) previewRequests.set(id, crypto.randomUUID());
      try {
        const receipt = await api('/api/events/apply', {method: 'POST',
          body: scopedBody({preview_id: id, request_id: previewRequests.get(id)})});
        dismissedPreviews.add(id);
        if (scope !== conversation) return;
        const receiptBox = $('#assistant-receipt');
        const undo = actionButton('Undo', async () => {
          if (scope !== conversation) return;
          undo.disabled = true;
          try {
            await api('/api/events/undo', {method: 'POST', body: scopedBody({preview_id: id})});
            receiptBox.replaceChildren(element('p', 'Previous settings restored.', 'fine'));
            await refresh();
          } catch (error) { notice(error.message); }
          finally { undo.disabled = false; }
        });
        receiptBox.replaceChildren(element('p', 'Updated ' + receipt.updated + ' records.', 'fine'), undo);
        await refresh();
      } catch (error) { notice(error.message); }
      finally { apply.disabled = false; }
    }, 'secondary');
    card.append(apply, actionButton('Dismiss', () => {
      dismissedPreviews.add(proposal.preview_id);
      renderAssistantPreviews(previews);
    }));
    target.append(card);
  }
  $('#assistant-toggle').textContent = pending.length ? '✧ Assistant · Review ready' : '✧ Assistant';
}

function clearEventState() {
  $('#bulk-editor').open = false;
  $('#bulk-form').reset();
  eventBoard = null;
  eventBirthdays = [];
  eventTasks = [];
  peopleDraft = null;
  calendarMonth = calendarDay = null;
  $('#people-panel').hidden = true;
  $('#gift-idea-panel').hidden = true;
  $('#gift-idea-form').reset();
  $('#calendar-grid').replaceChildren();
  $('#calendar-day').replaceChildren();
  birthdayReview = null;
  $('#birthday-sync-picker').hidden = true;
  $('#birthday-sync-people').querySelectorAll('label').forEach(label => label.remove());
  $('#birthday-selection-error').textContent = '';
  $('#birthday-selection-count').textContent = '';
  $('#assistant-calendar-review').hidden = true;
  $('#birthday-sync-status').textContent = '';
  $('#birthday-sync-toggle').hidden = $('#birthday-sync-now').hidden = true;
  populatePeopleSelect($('#birthday-form').elements.person, 'New person');
  $('#assistant-receipt').replaceChildren();
  renderAssistantPreviews([]);
}

function renderEventData(board, tasks, birthdays, previews, sync, privateChat) {
  eventBoard = board;
  eventTasks = tasks;
  eventBirthdays = birthdays;
  populatePeopleSelect($('#gift-idea-form').elements.person, 'New person', board?.people);
  populatePeopleSelect($('#birthday-form').elements.person, 'New person', board?.people);
  renderSavedIdeas();
  renderCalendar();
  renderAssistantPreviews(previews);
  renderSync(sync, privateChat);
}

function bindEvents() {
  $('#calendar-prev').onclick = () => shiftCalendar(-1);
  $('#calendar-next').onclick = () => shiftCalendar(1);
  $('#calendar-view').onclick = () => { agenda = !agenda; renderCalendar(); };
  const resync = async () => {
    const result = await api('/api/events/sync', {method: 'POST', body: scopedBody()});
    await refresh();
    if (result.state === 'unavailable') notice('Could not read Google Calendar.');
    else if (result.state === 'synced') notice('Calendar resynced.');
  };
  $('#birthday-sync-now').onclick = async () => {
    const button = $('#birthday-sync-now');
    button.disabled = true;
    try { await resync(); }
    catch (error) { notice(error.message); }
    finally { button.disabled = false; }
  };
  for (const [id, checked] of [['#birthday-select-all', true], ['#birthday-deselect-all', false]]) {
    $(id).onclick = () => {
      $('#birthday-sync-people').querySelectorAll('input').forEach(input => { input.checked = checked; });
      updateBirthdaySelectionCount();
    };
  }
  $('#birthday-close-picker').onclick = () => {
    birthdayReview = null;
    $('#birthday-sync-picker').hidden = true;
  };
  $('#birthday-save-selection').onclick = async event => {
    if (!birthdayReview) return;
    const target = conversation;
    const button = event.currentTarget;
    button.disabled = true;
    const selected = [...$('#birthday-sync-people').querySelectorAll('input:checked')]
      .map(input => Number(input.value));
    try {
      await api('/api/events/sync', {method: 'PUT', body: scopedBody({
        enabled: true, review_id: birthdayReview.review_id, selected,
      })});
      if (target !== conversation) return;
      birthdayReview = null;
      $('#birthday-sync-picker').hidden = true;
      await refresh();
      if (target === conversation) await resync();
    } catch (error) {
      if (target === conversation) notice(error.message);
    } finally { button.disabled = false; }
  };
  $('#birthday-pause-sync').onclick = async () => {
    const target = conversation;
    try {
      await api('/api/events/sync', {method: 'PUT', body: scopedBody({enabled: false})});
      if (target !== conversation) return;
      $('#birthday-sync-picker').hidden = true;
      await refresh();
    } catch (error) { notice(error.message); }
  };
  $('#assistant-calendar-open').onclick = async () => {
    setView('events');
    await openBirthdayPicker();
    $('#birthday-sync-picker').scrollIntoView({block: 'nearest'});
  };
  $('#bulk-form').onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = event.submitter;
    const budget = form.elements.budget.value;
    const cards = form.elements.cards.value;
    if (!budget && !cards) { notice('Choose a budget or card change.'); return; }
    button.disabled = true;
    try {
      await api('/api/events/preview', {method: 'POST', body: scopedBody({
        scope: form.elements.scope.value,
        ...(budget ? {budget: Number(budget)} : {}),
        ...(cards ? {cards: cards === 'yes'} : {}),
      })});
      $('#bulk-editor').open = false;
      openAssistant();
      await refresh();
      $('#assistant-previews summary')?.focus();
    } catch (error) { notice(error.message); }
    finally { button.disabled = false; }
  };
  $('#people-setup').onclick = () => {
    if (!eventBoard) return;
    peopleDraft ||= {
      ...structuredClone(eventBoard), step: 1,
      overrides: Object.fromEntries(eventBoard.people.map(person => [person.id, structuredClone(person.override)])),
    };
    $('#people-panel').hidden = false;
    renderPeopleStep();
  };
  $('#people-back').onclick = () => { peopleDraft.step--; renderPeopleStep(); };
  $('#people-close').onclick = () => { $('#people-panel').hidden = true; };
  $('#people-form').onsubmit = async event => {
    event.preventDefault();
    if (peopleDraft.step < 3) {
      peopleDraft.step++;
      renderPeopleStep();
      return;
    }
    const button = $('#people-next');
    button.disabled = true;
    try {
      const {defaults, overrides, revision} = peopleDraft;
      await api('/api/events/people', {method: 'PUT', body: scopedBody({
        defaults, overrides, revision, update_upcoming: event.currentTarget.elements.upcoming.checked,
      })});
      peopleDraft = null;
      $('#people-panel').hidden = true;
      await refresh();
      notice('People defaults saved.');
    } catch (error) {
      if (error.status === 409) {
        peopleDraft = null;
        $('#people-panel').hidden = true;
        await refresh().catch(() => {});
      }
      notice(error.message);
    } finally { button.disabled = false; }
  };
  $('#birthday-form').elements.person.onchange = event => {
    const person = eventBoard?.people.find(item => item.id === event.target.value);
    const recipient = $('#birthday-form').elements.recipient;
    if (person) recipient.value = person.name;
    recipient.readOnly = Boolean(person);
  };
  $('#gift-idea-open').onclick = () => {
    $('#gift-idea-panel').hidden = !$('#gift-idea-panel').hidden;
    if (!$('#gift-idea-panel').hidden) $('#gift-idea-form').elements.person.focus();
  };
  const form = $('#gift-idea-form');
  form.elements.person.onchange = renderSavedIdeas;
  let ideaDraft = null;
  form.onsubmit = async event => {
    event.preventDefault();
    const button = event.submitter;
    const {person, name, idea} = form.elements;
    if (!idea.value.trim() || (!person.value && !name.value.trim())) {
      notice('Enter a person and gift idea.');
      return;
    }
    const signature = JSON.stringify([conversation, person.value, name.value, idea.value]);
    if (ideaDraft?.signature !== signature) {
      ideaDraft = {signature, personId: person.value || crypto.randomUUID(), requestId: crypto.randomUUID()};
    }
    const scope = conversation;
    const attempt = ideaDraft;
    button.disabled = true;
    try {
      if (!person.value) await api('/api/events/people', {method: 'POST', body: scopedBody({
        name: name.value, person_id: attempt.personId,
      })});
      if (scope !== conversation) return;
      await api('/api/events/gift-ideas', {method: 'POST', body: scopedBody({
        person_id: attempt.personId, idea: idea.value, request_id: attempt.requestId,
      })});
      if (scope !== conversation) return;
      ideaDraft = null;
      idea.value = name.value = '';
      await refresh();
      person.value = attempt.personId;
      renderSavedIdeas();
      notice('Gift idea saved.');
    } catch (error) { notice(error.message); }
    finally { button.disabled = false; }
  };
}
