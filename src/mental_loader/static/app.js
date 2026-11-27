const $ = (selector) => document.querySelector(selector);
let conversation = null;
let me = null;
let poll = null;
let pendingRequest = null;
let refreshing = false;
let refreshAgain = false;
let activeRuns = false;
const ACTIVE_POLL_MS = 2500;
const IDLE_POLL_MS = 30000;
let lastMessageSignature = '';
let noticeTimer;

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
    throw new Error(message);
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
  $('#messages').replaceChildren();
  $('#tasks').replaceChildren();
  $('#workspace').hidden = true;
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
  conversation = rows.some((row) => row.id === preferred) ? preferred : rows[0].id;
  $('#conversations').replaceChildren();
  rows.forEach((row, index) => {
    const button = textElement('button', `Chat ${rows.length - index} · ${new Date(row.created_at).toLocaleDateString()}`);
    button.classList.toggle('active', row.id === conversation);
    button.onclick = () => loadConversations(row.id).then(refresh).catch((error) => notice(error.message));
    $('#conversations').append(button);
  });
  history.replaceState(null, '', `/?conversation=${encodeURIComponent(conversation)}`);
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
    api(`/api/conversations/${selectedConversation}/messages`), api('/api/tasks'),
  ]);
  const failure = results.find((result) => result.status === 'rejected');
  if (failure) throw failure.reason;
  const [data, tasks] = results.map((result) => result.value);
  if (conversation !== selectedConversation || $('#workspace').hidden) return;
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
    await api('/api/login', { method: 'POST', body: JSON.stringify({ access_code: $('#access-code').value.trim() }) });
    $('#access-code').value = '';
    await enter();
  } catch (error) { notice(error.message); }
  finally { button.disabled = false; }
};

$('#message-form').onsubmit = async (event) => {
  event.preventDefault();
  const content = $('#message-input').value.trim();
  if (!content || !conversation) return;
  $('#send').disabled = true;
  // Keep the same identity across a failed network request so retries do not duplicate work.
  if (!pendingRequest || pendingRequest.content !== content || pendingRequest.conversation !== conversation) {
    pendingRequest = { request_id: crypto.randomUUID(), content, conversation };
  }
  try {
    await api(`/api/conversations/${conversation}/messages`, {
      method: 'POST', body: JSON.stringify({ content, request_id: pendingRequest.request_id }),
    });
    pendingRequest = null;
    $('#message-input').value = '';
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
