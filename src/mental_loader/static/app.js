const $ = (selector) => document.querySelector(selector);
let conversation = null;
let me = null;
let poll = null;
let pendingRequest = null;
let refreshing = false;
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
  clearInterval(poll);
  poll = null;
  conversation = null;
  pendingRequest = null;
  lastMessageSignature = '';
  $('#message-input').value = '';
  drafts.clear();
  me = null;
  $('#new-private-chat').disabled = true;
  $('#messages').replaceChildren();
  $('#tasks').replaceChildren();
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
  history.replaceState(null, '', `/?conversation=${encodeURIComponent(conversation)}`);
  lastMessageSignature = '';
}

async function refresh() {
  if (!conversation || refreshing) return;
  refreshing = true;
  const selectedConversation = conversation;
  try {
    const [data, tasks] = await Promise.all([
      api(`/api/conversations/${selectedConversation}/messages`), api('/api/tasks'),
    ]);
    if (conversation !== selectedConversation || $('#workspace').hidden) return;
    const isPrivate = data.visibility === 'private';
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
    $('#run-status').textContent = data.active_runs.length ? 'Working… You can close this page and return later.' : '';
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
  } catch (error) {
    if (error.status === 404 && conversation === selectedConversation) {
      $('#messages').replaceChildren();
      $('#tasks').replaceChildren();
      $('#participants-panel').hidden = true;
      await loadConversations();
      notice('This chat is no longer available to your account.');
    } else { throw error; }
  } finally { refreshing = false; }
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
  $('#account-panel').hidden = !me.user_id;
  if (me.user_id) await loadConnections();
  $('#new-private-chat').disabled = !me.user_id;
  await loadConversations(new URLSearchParams(location.search).get('conversation'));
  await refresh();
  $('#send').disabled = false;
  clearInterval(poll);
  poll = setInterval(() => { if (!document.hidden) refresh().catch((error) => notice(error.message)); }, 2500);
}

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
    await loadConversations(row.id);
    await refresh();
  } catch (error) { notice(error.message); }
};
$('#new-private-chat').onclick = async () => {
  try {
    const row = await api('/api/conversations', {
      method: 'POST', body: JSON.stringify({ visibility: 'private' }),
    });
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
enter().catch((error) => { signedOut(); if (!error.message.includes('sign in')) notice(error.message); });
