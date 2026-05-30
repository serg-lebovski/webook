'use strict';

const STORAGE_KEY = 'webook_auth';
let serverUrl = '';
let token = '';

// ─── helpers ────────────────────────────────────────────────────────────────

function $(id) { return document.getElementById(id); }

function show(sectionId) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  $(sectionId).classList.add('active');
}

function showAlert(elementId, message, type) {
  const el = $(elementId);
  el.textContent = message;
  el.className = `alert alert-${type}`;
  el.style.display = 'block';
  if (type === 'success') {
    setTimeout(() => { el.style.display = 'none'; }, 3000);
  }
}

function hideAlert(elementId) {
  $(elementId).style.display = 'none';
}

// ─── API ─────────────────────────────────────────────────────────────────────

async function apiCall(method, path, body) {
  const base = serverUrl.replace(/\/$/, '');
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = 'Bearer ' + token;

  const res = await fetch(base + path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    let msg = `Ошибка ${res.status}`;
    try {
      const data = await res.json();
      msg = data.detail || msg;
    } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

// ─── folders ─────────────────────────────────────────────────────────────────

async function loadFolders() {
  try {
    const folders = await apiCall('GET', '/api/folders');
    const sel = $('folder-select');
    sel.innerHTML = '<option value="">Без папки</option>';
    folders.forEach(f => {
      const opt = document.createElement('option');
      opt.value = String(f.id);
      opt.textContent = f.name;
      sel.appendChild(opt);
    });
  } catch (_) { /* ignore — non-critical */ }
}

// ─── init ─────────────────────────────────────────────────────────────────────

async function init() {
  const stored = await chrome.storage.local.get(STORAGE_KEY);
  const auth = stored[STORAGE_KEY] || {};

  if (auth.token && auth.serverUrl) {
    serverUrl = auth.serverUrl;
    token = auth.token;
    await showSaveView();
  } else {
    if (auth.serverUrl) $('server-url').value = auth.serverUrl;
    show('section-login');
  }
}

async function showSaveView() {
  show('section-save');
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab) {
    $('tab-title').textContent = tab.title || tab.url || '—';
    $('tab-url').textContent = tab.url || '—';
  }
  await loadFolders();
}

// ─── login ───────────────────────────────────────────────────────────────────

$('btn-login').addEventListener('click', async () => {
  hideAlert('login-error');
  const url      = $('server-url').value.trim();
  const username = $('login-username').value.trim();
  const password = $('login-password').value;

  if (!url || !username || !password) {
    showAlert('login-error', 'Заполните все поля', 'error');
    return;
  }

  serverUrl = url;
  $('btn-login').textContent = 'Входим…';
  $('btn-login').disabled = true;

  try {
    const data = await apiCall('POST', '/api/token', { username, password });
    token = data.access_token;
    await chrome.storage.local.set({ [STORAGE_KEY]: { serverUrl: url, token } });
    await showSaveView();
  } catch (e) {
    showAlert('login-error', e.message, 'error');
  } finally {
    $('btn-login').textContent = 'Войти';
    $('btn-login').disabled = false;
  }
});

// ─── save link ───────────────────────────────────────────────────────────────

$('btn-save').addEventListener('click', async () => {
  hideAlert('save-alert');
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const folderId = $('folder-select').value || null;

  $('btn-save').textContent = 'Сохраняем…';
  $('btn-save').disabled = true;

  try {
    await apiCall('POST', '/api/links', {
      url: tab.url,
      title: tab.title || tab.url,
      folder_id: folderId ? parseInt(folderId, 10) : null,
    });
    showAlert('save-alert', 'Ссылка сохранена!', 'success');
  } catch (e) {
    showAlert('save-alert', e.message, 'error');
  } finally {
    $('btn-save').textContent = 'Сохранить ссылку';
    $('btn-save').disabled = false;
  }
});

// ─── new folder ──────────────────────────────────────────────────────────────

$('btn-toggle-new-folder').addEventListener('click', () => {
  const row = $('new-folder-row');
  const isVisible = row.style.display === 'block';
  row.style.display = isVisible ? 'none' : 'block';
  if (!isVisible) $('new-folder-name').focus();
});

$('btn-create-folder').addEventListener('click', async () => {
  const name = $('new-folder-name').value.trim();
  if (!name) return;

  $('btn-create-folder').disabled = true;
  try {
    const folder = await apiCall('POST', '/api/folders', { name });
    const sel = $('folder-select');
    const opt = document.createElement('option');
    opt.value = String(folder.id);
    opt.textContent = folder.name;
    sel.appendChild(opt);
    sel.value = String(folder.id);
    $('new-folder-name').value = '';
    $('new-folder-row').style.display = 'none';
  } catch (e) {
    showAlert('save-alert', e.message, 'error');
  } finally {
    $('btn-create-folder').disabled = false;
  }
});

// ─── logout / settings ───────────────────────────────────────────────────────

$('btn-logout').addEventListener('click', async () => {
  const stored = await chrome.storage.local.get(STORAGE_KEY);
  const prev = stored[STORAGE_KEY] || {};
  await chrome.storage.local.set({ [STORAGE_KEY]: { serverUrl: prev.serverUrl || '' } });
  token = '';
  if (prev.serverUrl) $('server-url').value = prev.serverUrl;
  show('section-login');
});

$('btn-settings').addEventListener('click', async () => {
  const stored = await chrome.storage.local.get(STORAGE_KEY);
  const prev = stored[STORAGE_KEY] || {};
  await chrome.storage.local.set({ [STORAGE_KEY]: { serverUrl: prev.serverUrl || '' } });
  token = '';
  $('server-url').value = prev.serverUrl || '';
  $('login-username').value = '';
  $('login-password').value = '';
  show('section-login');
});

// ─── start ───────────────────────────────────────────────────────────────────
init();
