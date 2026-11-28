const $ = (selector) => document.querySelector(selector);
let conversation = null;
let me = null;
let birthdayDraft = null;
let poll = null;
let pendingRequest = null;
let refreshing = false;
let refreshAgain = false;
let activeRuns = false;
const ACTIVE_POLL_MS = 2500;
const IDLE_POLL_MS = 30000;
let lastMessageSignature = '';
let noticeTimer;
const drafts = new Map();

function notice(message) {
  $('#notice').textContent = message;
  $('#notice').hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { $('#notice').hidden = true; }, 7000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  if (response.status === 401 && path !== '/api/login') {
    signedOut();
    throw new Error('Please sign in to your household.');
  }
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try { const data = await response.json(); if (typeof data.detail === 'string') message = data.detail; } catch {}
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function signedOut() {
  clearTimeout(poll);
  poll = null;
  conversation = null;
  pendingRequest = null;
  activeRuns = false;
  refreshAgain = false;
  lastMessageSignature = '';
  $('#message-input').value = '';
  drafts.clear();
  clearEventState();
  assistantOpen = false;
  me = null;
  $('#new-private-chat').disabled = true;
  reminderRequest = null;
  $('#reminder-form').reset();
  $('#reminder-feedback').textContent = '';
  setView('events');
  $('#messages').replaceChildren();
  $('#tasks').replaceChildren();
  $('#birthdays').replaceChildren();
  resetBirthday();
  $('#workspace').hidden = true;
  $('#participants-panel').hidden = true;
  $('#participants-list').replaceChildren();
  $('#calendar-events').replaceChildren();
  $('#account-panel').hidden = true;
  $('#login-screen').hidden = false;
}

function textElement(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

async function loadConversations(preferred) {
  const rows = await api('/api/conversations');
  if (!rows.length) {
    const row = await api('/api/conversations', { method: 'POST' });
    return loadConversations(row.id);
  }
  const next = rows.some((row) => row.id === preferred) ? preferred : rows[0].id;
  if (next !== conversation) {
    clearEventState();
    resetBirthday();
    $('#birthdays').replaceChildren();
    if (conversation) drafts.set(conversation, $('#message-input').value);
    $('#message-input').value = drafts.get(next) || '';
    $('#messages').replaceChildren();
    $('#tasks').replaceChildren();
    $('#participants-panel').open = false;
    $('#participants-panel').hidden = true;
    $('#participants-list').replaceChildren();
    $('#participant-select').replaceChildren();
  }
  conversation = next;
  $('#conversations').replaceChildren();
  rows.forEach((row, index) => {
    const kind = row.visibility === 'private' ? 'Private chat' : 'Household chat';
    const button = textElement('button', `${kind} ${rows.length - index} · ${new Date(row.created_at).toLocaleDateString()}`);
    button.classList.toggle('active', row.id === conversation);
    button.onclick = () => loadConversations(row.id).then(refresh).catch((error) => notice(error.message));
    $('#conversations').append(button);
  });
  history.replaceState(null, '', `/?conversation=${encodeURIComponent(conversation)}&view=${currentView}`);
  lastMessageSignature = '';
}

async function refresh() {
  clearTimeout(poll);
  poll = null;
  if (!conversation || document.hidden || $('#workspace').hidden) return;
  // Actions and conversation switches during a fetch must get a fresh read afterwards.
  if (refreshing) { refreshAgain = true; return; }
  refreshing = true;
  try {
    do {
      refreshAgain = false;
      await refreshOnce();
    } while (refreshAgain && conversation && !document.hidden && !$('#workspace').hidden);
  } finally {
    refreshing = false;
    if (conversation && !document.hidden && !$('#workspace').hidden) {
      poll = setTimeout(() => refresh().catch((error) => notice(error.message)),
        activeRuns ? ACTIVE_POLL_MS : IDLE_POLL_MS);
    }
  }
}

async function refreshOnce() {
  const selectedConversation = conversation;
  // Wait for both reads even if one fails, so retries cannot overlap a slow sibling.
  const results = await Promise.allSettled([
    api(`/api/conversations/${selectedConversation}/messages`), api('/api/tasks'), api('/api/birthdays'),
    api(`/api/events/${selectedConversation}`), api(`/api/events/${selectedConversation}/previews`),
    api(`/api/events/${selectedConversation}/sync`),
  ]);
  const failure = results.find((result) => result.status === 'rejected');
  if (failure) {
    if (conversation !== selectedConversation || $('#workspace').hidden) return;
    if (failure.reason.status === 404) {
      $('#messages').replaceChildren();
      $('#tasks').replaceChildren();
      $('#birthdays').replaceChildren();
      resetBirthday();
      $('#participants-panel').hidden = true;
      await loadConversations();
      refreshAgain = true;
      notice('This chat is no longer available to your account.');
      return;
    }
    throw failure.reason;
  }
  const [data, tasks, birthdays, board, previews, sync] = results.map((result) => result.value);
  if (conversation !== selectedConversation || $('#workspace').hidden) return;
  renderBirthdays(birthdays);
  const isPrivate = data.visibility === 'private';
  renderEventData(board, tasks, birthdays, previews, sync, isPrivate);
  $('#record-scope').textContent = `${isPrivate ? 'Private chat · only participants' : 'Household chat · shared with your household'} — birthdays and new reminders belong to the selected chat. Upcoming steps include all reminders you can access. Timezone: ${me.timezone}.`;
  $('#chat-heading').textContent = isPrivate ? 'Private chat' : 'Household chat';
  $('#chat-visibility').textContent = isPrivate ? 'Only chat participants' : 'Shared with your household';
  $('#chat-guidance').textContent = isPrivate && !data.ai_available
    ? 'Only participants can see this chat and its reminders. Use /remind; AI is unavailable in private chats.'
    : isPrivate ? 'Only participants can see this chat. AI uses this chat’s separate Hermes runtime.'
    : '/remind works in code without credits. Natural chat uses Hermes when enabled.';
  $('#try-ideas').hidden = isPrivate && !data.ai_available;
  $('#participants-panel').hidden = !data.can_manage_participants;
  const signature = JSON.stringify(data.messages);
  if (signature !== lastMessageSignature) {
    lastMessageSignature = signature;
    const messages = $('#messages');
    const wasNearBottom = messages.scrollHeight - messages.scrollTop - messages.clientHeight < 100;
    messages.replaceChildren();
    if (!data.messages.length) {
      const welcome = textElement('div', '', 'welcome');
      welcome.append(textElement('div', '✧', 'welcome-symbol'),
        textElement('h2', 'What’s on your mind?'),
        textElement('p', 'Start small. Save one reminder, close the page, and come back to find it here.'));
      messages.append(welcome);
    }
    data.messages.forEach((message) => {
      const bubble = textElement('div', '', `message ${message.role}`);
      bubble.append(textElement('span', message.role === 'user' ? 'You' : 'Mental Loader', 'message-label'),
        document.createTextNode(message.content));
      messages.append(bubble);
    });
    if (wasNearBottom) messages.scrollTop = messages.scrollHeight;
  }
  activeRuns = data.active_runs.length > 0;
  $('#run-status').textContent = activeRuns ? 'Working… You can close this page and return later.' : '';
  const open = tasks.filter((task) => task.status === 'open');
  $('#task-count').textContent = open.length;
  $('#tasks').replaceChildren();
  if (!open.length) $('#tasks').append(textElement('p', 'Nothing to carry here yet.', 'empty'));
  open.forEach((task) => {
    const item = textElement('div', '', 'task');
    const complete = textElement('button', '✓');
    complete.setAttribute('aria-label', `Complete ${task.title}`);
    complete.onclick = async () => {
      complete.disabled = true;
      try { await api(`/api/tasks/${task.id}/complete`, { method: 'POST' }); await refresh(); }
      catch (error) { notice(error.message); complete.disabled = false; }
    };
    const description = document.createElement('div');
    description.append(textElement('strong', task.title), textElement('small',
      new Date(task.due_at).toLocaleString([], { timeZone: me.timezone, dateStyle: 'medium', timeStyle: 'short' })));
    const delivery = task.push_status.length ? `Push: ${task.push_status.join(', ')}`
      : task.notified_at ? 'Added to chat · no subscribed devices at due time' : 'Reminder scheduled';
    description.append(textElement('small', delivery));
    item.append(complete, description);
    $('#tasks').append(item);
  });
}

async function enter() {
  me = await api('/api/me');
  $('#household-name').textContent = me.household;
  $('#ai-badge').textContent = me.ai_enabled ? 'Hermes enabled · request limit' : 'Hermes off · No model spend';
  $('#push-status').textContent = me.push_configured ? 'Ready to connect this device.' : 'Generate VAPID keys to enable push.';
  $('#login-screen').hidden = true;
  $('#workspace').hidden = false;
  $('#credential-result').hidden = true;
  $('#new-credentials').value = '';
  setView(currentView);
  if (me.user_id) await loadConnections();
  $('#new-private-chat').disabled = !me.user_id;
  await loadConversations(new URLSearchParams(location.search).get('conversation'));
  await refresh();
  $('#send').disabled = false;
}

document.addEventListener('visibilitychange', () => {
  clearTimeout(poll);
  poll = null;
  if (!document.hidden) refresh().catch((error) => notice(error.message));
});

$('#login-form').onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const access_code = $('#access-code').value.trim();
    const choices = await api('/api/login/options', {
      method: 'POST', body: JSON.stringify({ access_code }),
    });
    const selected = $('#household-select').value;
    if (choices.length > 1 && !choices.some((row) => row.id === selected)) {
      $('#household-select').replaceChildren(textElement('option', 'Choose a household'));
      $('#household-select').firstChild.value = '';
      choices.forEach((row) => {
        const option = textElement('option', row.name); option.value = row.id;
        $('#household-select').append(option);
      });
      $('#household-select').hidden = false;
      $('#household-select-label').hidden = false;
      return;
    }
    // A browser changing accounts must stop the previous account's device delivery.
    // This also works when its old app session has expired and cannot unsubscribe by API.
    const registration = await navigator.serviceWorker?.getRegistration();
    const subscription = await registration?.pushManager?.getSubscription();
    if (subscription) await subscription.unsubscribe();
    await api('/api/login', { method: 'POST', body: JSON.stringify({
      access_code, household_id: choices.length === 1 ? choices[0].id : selected,
    }) });
    $('#access-code').value = '';
    $('#household-select').replaceChildren();
    $('#household-select').hidden = true;
    $('#household-select-label').hidden = true;
    await enter();
  } catch (error) { notice(error.message); }
  finally { button.disabled = false; }
};

$('#message-form').onsubmit = async (event) => {
  event.preventDefault();
  const content = $('#message-input').value.trim();
  if (!content || !conversation) return;
  $('#send').disabled = true;
  const target = conversation;
  // Keep the same identity across a failed network request so retries do not duplicate work.
  if (!pendingRequest || pendingRequest.content !== content || pendingRequest.conversation !== conversation) {
    pendingRequest = { request_id: crypto.randomUUID(), content, conversation };
  }
  try {
    const attempt = pendingRequest;
    await api(`/api/conversations/${target}/messages`, {
      method: 'POST', body: JSON.stringify({ content, request_id: pendingRequest.request_id }),
    });
    if (pendingRequest === attempt) pendingRequest = null;
    drafts.delete(target);
    if (conversation === target && $('#message-input').value.trim() === content) {
      $('#message-input').value = '';
    }
    await refresh();
  } catch (error) { notice(`${error.message} Your draft is still here.`); }
  finally { $('#send').disabled = false; }
};

$('#new-chat').onclick = async () => {
  try {
    const row = await api('/api/conversations', { method: 'POST' });
    setView('assistant');
    await loadConversations(row.id);
    await refresh();
  } catch (error) { notice(error.message); }
};
$('#new-private-chat').onclick = async () => {
  try {
    const row = await api('/api/conversations', {
      method: 'POST', body: JSON.stringify({ visibility: 'private' }),
    });
    setView('assistant');
    await loadConversations(row.id);
    await refresh();
  } catch (error) { notice(error.message); }
};

async function loadParticipants() {
  const selected = conversation;
  const [participants, eligible] = await Promise.all([
    api(`/api/conversations/${selected}/participants`),
    api(`/api/conversations/${selected}/eligible-participants`),
  ]);
  if (conversation !== selected || $('#workspace').hidden) return;
  $('#participants-list').replaceChildren();
  participants.forEach((person) => {
    const item = textElement('li', `${person.name}${person.owner ? ' (creator)' : ''} `);
    if (!person.owner) {
      const remove = textElement('button', 'Remove', 'quiet');
      remove.setAttribute('aria-label', `Remove ${person.name} from chat`);
      remove.onclick = async () => {
        remove.disabled = true;
        try {
          await api(`/api/conversations/${selected}/participants/${person.user_id}`, { method: 'DELETE' });
          await loadParticipants();
        } catch (error) { notice(error.message); remove.disabled = false; }
      };
      item.append(remove);
    }
    $('#participants-list').append(item);
  });
  const choices = eligible.filter((person) => !participants.some((p) => p.user_id === person.user_id));
  $('#participant-select').replaceChildren();
  choices.forEach((person) => {
    const option = textElement('option', person.name);
    option.value = person.user_id;
    $('#participant-select').append(option);
  });
  if (!choices.length) {
    $('#participant-select').append(textElement('option', 'No other members available'));
  }
  $('#participant-select').disabled = !choices.length;
  $('#add-participant').disabled = !choices.length;
}
$('#participants-panel').ontoggle = () => {
  if ($('#participants-panel').open) loadParticipants().catch((error) => notice(error.message));
};
$('#participant-form').onsubmit = async (event) => {
  event.preventDefault();
  const user_id = $('#participant-select').value;
  if (!conversation || !user_id) return;
  $('#add-participant').disabled = true;
  try {
    await api(`/api/conversations/${conversation}/participants`, {
      method: 'POST', body: JSON.stringify({ user_id }),
    });
    await loadParticipants();
  } catch (error) { notice(error.message); $('#add-participant').disabled = false; }
};

async function loadConnections() {
  const data = await api('/api/integrations');
  const connected = data.connected.includes('google-calendar');
  $('#calendar-status').textContent = connected ? 'Your calendar is connected with read-only access.'
    : me.google_available ? 'Calendar not connected.' : 'Google connection needs local OAuth client setup.';
  $('#connect-calendar').disabled = !me.google_available;
  $('#connect-calendar').hidden = connected;
  $('#disconnect-calendar').hidden = !connected;
  $('#read-calendar').hidden = !connected;
  if (!connected) $('#calendar-events').replaceChildren();
}
$('#connect-calendar').onclick = async () => {
  try {
    const data = await api('/api/integrations/google/connect', { method: 'POST' });
    const url = new URL(data.authorization_url);
    if (url.protocol !== 'https:' || url.hostname !== 'accounts.google.com') throw new Error('Invalid authorization destination.');
    location.assign(url.href);
  } catch (error) { notice(error.message); }
};
$('#disconnect-calendar').onclick = async () => {
  try {
    await api('/api/integrations/google', { method: 'DELETE' });
    await loadConnections();
  } catch (error) { notice(error.message); }
};
$('#read-calendar').onclick = async () => {
  try {
    const data = await api('/api/integrations/google/events');
    $('#calendar-events').replaceChildren();
    data.events.forEach((item) => {
      $('#calendar-events').append(textElement('li', `${item.summary || 'Untitled event'} · ${item.start?.dateTime || item.start?.date || ''}`));
    });
    if (!data.events.length) $('#calendar-events').append(textElement('li', 'No upcoming events.'));
  } catch (error) { notice(error.message); }
};
function showCredentials(data) {
  signedOut();
  $('#credential-result').hidden = false;
  $('#new-credentials').value = `Personal code:\n${data.access_code}\n\nRecovery code:\n${data.recovery_code}`;
}
$('#recovery-form').onsubmit = async (event) => {
  event.preventDefault();
  event.submitter.disabled = true;
  try {
    const data = await api('/api/account/recover', {
      method: 'POST', body: JSON.stringify({ recovery_code: $('#recovery-code').value.trim() }),
    });
    $('#recovery-code').value = '';
    showCredentials(data);
  } catch (error) { notice(error.message); }
  finally { event.submitter.disabled = false; }
};
$('#rotate-form').onsubmit = async (event) => {
  event.preventDefault();
  event.submitter.disabled = true;
  try {
    const data = await api('/api/account/rotate', {
      method: 'POST', body: JSON.stringify({ access_code: $('#current-code').value.trim() }),
    });
    $('#current-code').value = '';
    showCredentials(data);
  } catch (error) { notice(error.message); }
  finally { event.submitter.disabled = false; }
};
$('#try-reminder').onclick = () => { $('#message-input').value = '/remind 1m Buy a birthday card'; $('#message-input').focus(); };
$('#try-ideas').onclick = () => { $('#message-input').value = 'Suggest three thoughtful birthday gifts under $40 for someone who enjoys gardening'; $('#message-input').focus(); };

$('#logout').onclick = async () => {
  try {
    const registration = await navigator.serviceWorker?.getRegistration();
    const subscription = await registration?.pushManager?.getSubscription();
    if (subscription) {
      await api('/api/push-subscriptions', { method: 'DELETE', body: JSON.stringify(subscription.toJSON()) });
      await subscription.unsubscribe();
    }
    await api('/api/logout', { method: 'POST' });
    signedOut();
  } catch (error) { notice(error.message); }
};

$('#notifications').onclick = async () => {
  try {
    if (!window.isSecureContext) throw new Error('Push needs HTTPS on your phone (localhost works on a laptop).');
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      throw new Error('Push is unavailable. On iPhone, add this app to the Home Screen and open it there.');
    }
    if (!me.push_configured) throw new Error('Run the local init command and configure VAPID_PUBLIC_KEY first.');
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') throw new Error('Notifications were not allowed. Check your browser settings.');
    const registration = await navigator.serviceWorker.ready;
    const raw = me.vapid_public_key.replace(/-/g, '+').replace(/_/g, '/');
    const key = Uint8Array.from(atob(raw + '='.repeat((4 - raw.length % 4) % 4)), (c) => c.charCodeAt(0));
    const subscription = await registration.pushManager.getSubscription() || await registration.pushManager.subscribe({
      userVisibleOnly: true, applicationServerKey: key,
    });
    await api('/api/push-subscriptions', { method: 'POST', body: JSON.stringify(subscription.toJSON()) });
    $('#push-status').textContent = 'This device is connected. Try a 1-minute reminder, then close the app.';
    notice('Notifications enabled.');
  } catch (error) { notice(error.message); }
};

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => notice('Service worker unavailable. Chat still works.'));


function resetBirthday() {
  birthdayDraft = null;
  $('#birthday-feedback').textContent = '';
  $('#birthday-form').reset();
  $('#birthday-form').elements.occasion_date.disabled = false;
  $('#birthday-form').elements.recipient.readOnly = false;
  $('#birthday-person-label').hidden = false;
  $('#birthday-legacy-fields').hidden = true;
}
function renderBirthdays(rows) {
  const list = $('#birthdays');
  const visible = rows.filter((row) => row.conversation_id === conversation);
  const signature = JSON.stringify(visible);
  if (list.dataset.signature === signature && list.childNodes.length) return;
  const expanded = new Set([...list.querySelectorAll('details[open]')].map(item => item.id));
  list.dataset.signature = signature;
  list.replaceChildren();
  if (!visible.length) list.append(textElement('p', 'Birthdays appear here after syncing. You can also add one manually.', 'empty'));
  visible.forEach((row) => {
    const card = textElement('div', '', 'birthday-card');
    card.id = 'birthday-' + row.id;
    card.append(birthdayPlanControl(row));
    card.querySelector('details').id = 'birthday-plan-' + row.id;
    card.querySelector('details').open = expanded.has('birthday-plan-' + row.id);
    card.append(textElement('strong', row.recipient),
      textElement('p', `${row.occasion_date} · ${row.calendar_id ? 'Google Calendar · ' : 'Manual · '}${row.gift_status}`),
      textElement('p', row.budget === null ? 'Budget not set' : `${row.currency} ${row.budget}`));
    card.append(card.firstChild);
    const edit = textElement('button', 'Edit', 'quiet');
    edit.onclick = () => {
      birthdayDraft = row;
      const form = $('#birthday-form');
      ['recipient', 'occasion_date', 'preferences', 'budget', 'currency', 'lead_days', 'gift_status'].forEach((key) => { form.elements[key].value = row[key] ?? ''; });
      form.elements.occasion_date.disabled = !!row.calendar_id;
      form.elements.recipient.readOnly = Boolean(row.calendar_id || row.person_id);
      $('#birthday-person-label').hidden = true;
      $('#birthday-legacy-fields').hidden = Boolean(row.plan);
      $('#birthday-editor').open = true;
    };
    const ideas = textElement('button', 'Gift ideas', 'quiet');
    ideas.onclick = () => {
      $('#message-input').value = `Suggest three gifts for birthday ${row.id}. Use the saved ideas and budget from the application. These are proposals only.`;
      openAssistant();
      $('#message-input').focus();
    };
    card.append(edit, ideas);
    $('#birthdays').append(card);
  });
}
$('#birthday-new').onclick = resetBirthday;
$('#birthday-form').onsubmit = async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = event.submitter;
  button.disabled = true;
  $('#birthday-feedback').textContent = 'Saving…';
  if (!birthdayDraft) birthdayDraft = {id: crypto.randomUUID(), conversation_id: conversation, newRecord: true};
  const attempt = birthdayDraft;
  const body = { conversation_id: birthdayDraft.conversation_id };
  ['recipient', 'occasion_date', 'preferences', 'currency', 'gift_status'].forEach((key) => { body[key] = form.elements[key].value; });
  body.lead_days = Number(form.elements.lead_days.value);
  body.budget = form.elements.budget.value || null;
  try {
    if (attempt.newRecord) body.person_id = form.elements.person.value || null;
    const endpoint = attempt.newRecord ? '/api/events/birthdays/' : '/api/birthdays/';
    await api(endpoint + attempt.id, {method: 'PUT', body: JSON.stringify(body)});
    if (birthdayDraft !== attempt) return;
    resetBirthday();
    $('#birthday-editor').open = false;
    await refresh();
    notice('Birthday saved.');
  } catch (error) { $('#birthday-feedback').textContent = `${error.message} Your draft is still here.`; }
  finally { button.disabled = false; }
};

const viewCopy = {
  events: ['Remember. Prepare. Enjoy.', 'Room for the good things.', 'Birthdays and the little steps that make them thoughtful.'],
  assistant: ['A little help when you need it', 'Think it through together.', 'Ask for ideas, untangle a plan, or save a reminder.'],
  meals: ['Around your table', 'What sounds good?', 'Meal planning is coming next.'],
  shopping: ['Ready for the week', 'One less forgotten thing.', 'A shared shopping list is coming next.'],
  stock: ['Know what’s on hand', 'A little more prepared.', 'Stock and supplies are coming next.'],
  settings: ['Your household, your way', 'Make yourself at home.', 'Your account, connections and notifications.'],
};
let currentView = 'events';
let assistantOpen = false;
function openAssistant() {
  if (currentView !== 'events') setView('assistant');
  else { assistantOpen = true; setView('events'); }
  $('#message-input').focus();
}
function setView(view, focus = false) {
  if (!viewCopy[view]) view = 'events';
  currentView = view;
  if (conversation) history.replaceState(null, '', `/?conversation=${encodeURIComponent(conversation)}&view=${view}`);
  const [eyebrow, title, description] = viewCopy[view];
  $('#view-eyebrow').textContent = eyebrow;
  $('#view-title').textContent = title;
  $('#view-description').textContent = description;
  document.querySelectorAll('[data-view]').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === view);
    if (button.dataset.view === view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $('#events-workspace').hidden = view !== 'events';
  $('#events-panel').hidden = view !== 'events';
  $('#view-grid').classList.toggle('events-with-assistant', view === 'events' && assistantOpen);
  $('#assistant-toggle').hidden = view !== 'events';
  $('#assistant-toggle').setAttribute('aria-expanded', String(view === 'events' && assistantOpen));
  $('#upcoming-panel').hidden = view !== 'events';
  $('#assistant-panel').hidden = view !== 'assistant' && !(view === 'events' && assistantOpen);
  $('#notification-panel').hidden = view !== 'settings';
  $('#account-panel').hidden = view !== 'settings' || !me?.user_id;
  $('#setup-panel').hidden = view !== 'settings';
  const future = ['meals', 'shopping', 'stock'].includes(view);
  $('#future-panel').hidden = !future;
  $('#view-grid').hidden = future;
  $('#record-scope').hidden = !['events', 'assistant'].includes(view);
  if (future) {
    $('#future-heading').textContent = title;
    $('#future-description').textContent = {
      meals: 'Favourite meals, dietary needs and cooking time will help shape useful plans. Usual staples will stay separate from confirmed stock.',
      shopping: 'Add and complete items directly, with the assistant helping you work out what you need.',
      stock: 'Record what you have and when you checked it. Remembered staples alone will never count as current stock.',
    }[view];
  }
  if (focus) $('#view-title').focus();
}
document.querySelectorAll('[data-view]').forEach((button) => {
  button.onclick = () => {
    if (button.dataset.view === 'assistant' && currentView === 'events') openAssistant();
    else setView(button.dataset.view, true);
  };
});
$('#assistant-toggle').onclick = () => {
  if (assistantOpen) { assistantOpen = false; setView('events'); }
  else openAssistant();
};
$('#assistant-close').onclick = () => {
  assistantOpen = false;
  setView('events');
  $('#assistant-toggle').focus();
};
$('#horizon-slot').append($('#upcoming-panel'));
bindEvents();
resetBirthday();
$('#future-assistant').onclick = () => setView('assistant', true);
setView(new URLSearchParams(location.search).get('view') || (new URLSearchParams(location.search).has('conversation') ? 'assistant' : 'events'));
$('#reminder-timezone').textContent = `Enter time in your device timezone (${Intl.DateTimeFormat().resolvedOptions().timeZone}). Saved reminders display in your household timezone.`;

let reminderRequest = null;
$('#reminder-form').onsubmit = async (event) => {
  event.preventDefault();
  if (!conversation) return;
  const form = event.currentTarget;
  const button = event.submitter;
  const body = {title: form.elements.title.value.trim(), due_at: new Date(form.elements.due_at.value).toISOString(), conversation_id: conversation};
  const signature = JSON.stringify(body);
  if (!reminderRequest || reminderRequest.signature !== signature) {
    reminderRequest = {signature, body: {...body, request_id: crypto.randomUUID()}};
  }
  const attempt = reminderRequest;
  button.disabled = true;
  $('#reminder-feedback').textContent = 'Saving…';
  try {
    await api('/api/reminders', {method: 'POST', body: JSON.stringify(attempt.body)});
    if (reminderRequest === attempt) { reminderRequest = null; form.reset(); }
    $('#reminder-feedback').textContent = 'Reminder saved.';
    await refresh();
  } catch (error) { $('#reminder-feedback').textContent = `${error.message} Your draft is still here.`; }
  finally { button.disabled = false; }
};

enter().catch((error) => { signedOut(); if (!error.message.includes('sign in')) notice(error.message); });
