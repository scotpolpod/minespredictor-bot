const tg = window.Telegram?.WebApp;
if (tg) {
  tg.expand();
  tg.setHeaderColor('#0d0d14');
  function setTgHeight() {
    var h = (tg.viewportStableHeight || tg.viewportHeight || window.innerHeight) + 'px';
    document.documentElement.style.setProperty('--tg-height', h);
  }
  setTgHeight();
  tg.onEvent('viewportChanged', setTgHeight);
}

const GRID_SIZE      = 25;
const CRYSTAL_EMOJI  = '💎';
const MINE_EMOJI     = '💣';
const OPTIMIZE_KEY   = 'mp_last_optimize';

// Параметры только из URL — localStorage не используется как fallback для безопасности
const _params = new URLSearchParams(window.location.search);
const _uid    = _params.get('uid')  || '';
const _days   = parseInt(_params.get('days') || '0');

// Если пришли через бот — сохраняем для статистики
if (_uid) localStorage.setItem('mp_uid', _uid);

// ── GATE: блокируем доступ без uid ───────────────────────
if (!_uid) {
  document.getElementById('gate-screen').style.display    = 'flex';
  document.getElementById('main-screen').style.display    = 'none';
  document.getElementById('optimize-screen').style.display = 'none';
}

let vavadaId = _uid;
let penBlocked = 4;

/* ════════════════════════════════
   TABS
════════════════════════════════ */
function switchTab(tab) {
  document.getElementById('page-mines').style.display   = tab === 'mines'   ? 'flex' : 'none';
  document.getElementById('page-penalty').style.display = tab === 'penalty' ? 'flex' : 'none';
  document.getElementById('page-sub').style.display     = tab === 'sub'     ? 'flex' : 'none';
  document.getElementById('tab-mines').classList.toggle('active',   tab === 'mines');
  document.getElementById('tab-penalty').classList.toggle('active', tab === 'penalty');
  document.getElementById('tab-sub').classList.toggle('active',     tab === 'sub');
  var logos = { mines: '💎 MinesPredictor', penalty: '⚽ PenaltyPredictor', sub: '🔑 MinesPredictor' };
  document.getElementById('header-logo').textContent = logos[tab] || '💎 MinesPredictor';
}

/* ════════════════════════════════
   VAVADA DISPLAY
════════════════════════════════ */
function updateVavadaDisplay() {
  var text   = vavadaId ? '🎰 ID: ' + vavadaId : '🎰 Brak ID — ustaw w bocie';
  var hasCls = vavadaId ? 'add' : 'remove';
  var el  = document.getElementById('vavada-display');
  var el2 = document.getElementById('vavada-display-pen');
  if (el)  { document.getElementById('vavada-id-text').textContent = text;     el.classList[hasCls]('has-id'); }
  if (el2) { document.getElementById('vavada-id-text-pen').textContent = text; el2.classList[hasCls]('has-id'); }
}


/* ════════════════════════════════
   MINES
════════════════════════════════ */
function syncDifficulty() {
  document.getElementById('diff-label').textContent = document.getElementById('difficulty').value;
}

function getSignal() {
  var btn = document.getElementById('signal-btn');
  btn.classList.add('loading');
  btn.innerHTML = '<span class="dots">Analizuję</span>';
  setTimeout(function() {
    generatePrediction();
    btn.classList.remove('loading');
    btn.innerHTML = '<span class="btn-icon">📡</span> Pobierz Sygnał';
  }, 1200 + Math.random() * 800);
}

function generatePrediction() {
  var mines   = parseInt(document.getElementById('difficulty').value);
  var crystals = GRID_SIZE - mines;
  var indices = Array.from({length: GRID_SIZE}, function(_, i) { return i; });
  for (var i = indices.length - 1; i > 0; i--) {
    var j = Math.floor(Math.random() * (i + 1));
    var t = indices[i]; indices[i] = indices[j]; indices[j] = t;
  }
  var mineSet = new Set(indices.slice(0, mines));
  var data = Array.from({length: GRID_SIZE}, function(_, i) { return mineSet.has(i) ? 'mine' : 'crystal'; });
  renderGrid(data);
  updateStats(crystals, mines);
}

function initGrid() {
  var grid = document.getElementById('grid');
  grid.innerHTML = '';
  for (var i = 0; i < GRID_SIZE; i++) {
    var cell = document.createElement('div');
    cell.className = 'cell empty';
    cell.style.setProperty('--i', i);
    grid.appendChild(cell);
  }
}

function renderGrid(data) {
  var cells = document.querySelectorAll('.cell');
  cells.forEach(function(cell, i) {
    cell.className = 'cell';
    cell.textContent = '';
    setTimeout(function() {
      cell.classList.add(data[i], 'revealing');
      cell.textContent = data[i] === 'crystal' ? CRYSTAL_EMOJI : MINE_EMOJI;
    }, i * 40);
  });
}

function updateStats(crystals, mines) {
  var acc = (85 + Math.floor(Math.random() * 12)) + '%';
  setTimeout(function() {
    document.getElementById('stat-crystals').textContent = crystals;
    document.getElementById('stat-mines').textContent    = mines;
    document.getElementById('stat-accuracy').textContent = acc;
    document.querySelectorAll('#page-mines .stat-box').forEach(function(b) { b.classList.add('active'); });
  }, GRID_SIZE * 40 + 100);
}

/* ════════════════════════════════
   PENALTY
════════════════════════════════ */
var ZONE_NAMES = ['GÓR-L','GÓR-C','GÓR','GÓR-C','GÓR-R','ŚR-L','ŚR-C','ŚR','ŚR-C','ŚR-R','DÓŁ-L','DÓŁ-C','DÓŁ','DÓŁ-C','DÓŁ-R'];

function setPenDiff(btn, label, blocked) {
  document.querySelectorAll('.pen-diff-btn').forEach(function(b) { b.classList.remove('active'); });
  btn.classList.add('active');
  document.getElementById('pen-diff-label').textContent = label;
  penBlocked = blocked;
  initGoal();
}

function initGoal() {
  var net = document.getElementById('goal-net');
  net.innerHTML = '';
  for (var i = 0; i < 15; i++) {
    var cell = document.createElement('div');
    cell.className = 'goal-cell';
    cell.dataset.idx = i;
    net.appendChild(cell);
  }
}

function getPenaltySignal() {
  var btn = document.getElementById('pen-signal-btn');
  btn.classList.add('loading');
  btn.innerHTML = '<span class="dots">Analizuję</span>';
  setTimeout(function() {
    renderPenaltySignal();
    btn.classList.remove('loading');
    btn.innerHTML = '<span class="btn-icon">📡</span> Pobierz Sygnał';
  }, 1200 + Math.random() * 800);
}

function renderPenaltySignal() {
  var cells = document.querySelectorAll('.goal-cell');
  var total = 15;
  var indices = Array.from({length: total}, function(_, i) { return i; });
  for (var i = indices.length - 1; i > 0; i--) {
    var j = Math.floor(Math.random() * (i + 1));
    var t = indices[i]; indices[i] = indices[j]; indices[j] = t;
  }
  var blockedSet  = new Set(indices.slice(0, penBlocked));
  var safe        = indices.slice(penBlocked);
  var signalCount = Math.floor(Math.random() * 3) + 1;
  var signalSet   = new Set(safe.slice(0, signalCount));
  cells.forEach(function(cell, i) {
    cell.className = 'goal-cell';
    cell.textContent = '';
    setTimeout(function() {
      cell.classList.add('revealing-goal');
      if (signalSet.has(i)) { cell.classList.add('signal-zone'); cell.textContent = '✅'; }
    }, i * 35);
  });
  var chance  = (72 + Math.floor(Math.random() * 18)) + '%';
  var rowName = signalCount + (signalCount === 1 ? ' strefa' : ' strefy');
  var acc     = (83 + Math.floor(Math.random() * 12)) + '%';
  setTimeout(function() {
    document.getElementById('pen-stat-zone').textContent     = rowName;
    document.getElementById('pen-stat-chance').textContent   = chance;
    document.getElementById('pen-stat-accuracy').textContent = acc;
    document.querySelectorAll('#page-penalty .stat-box').forEach(function(b) { b.classList.add('active'); });
  }, 15 * 35 + 100);
}

/* ════════════════════════════════
   SUBSCRIPTION TAB
════════════════════════════════ */
function initSubTab() {
  var days  = _days;
  var card  = document.getElementById('sub-card');
  var icon  = document.getElementById('sub-card-icon');
  var title = document.getElementById('sub-card-title');
  var det   = document.getElementById('sub-card-detail');

  if (days > 0) {
    card.classList.add('active');
    icon.textContent  = '✅';
    title.textContent = 'Subskrypcja aktywna';
    det.textContent   = 'Pozostało: ' + days + ' dni';
  } else {
    card.classList.add('inactive');
    icon.textContent  = '🔒';
    title.textContent = 'Brak subskrypcji';
    det.textContent   = 'Aktywuj kod lub kup plan poniżej';
  }

  // Kupno — otwiera czat @rmpl13
  addBtn('btn-buy', function() {
    if (tg && tg.openTelegramLink) tg.openTelegramLink('https://t.me/rmpl13');
    else window.open('https://t.me/rmpl13', '_blank');
  });

  // Aktywacja kodu
  addBtn('code-activate-btn', function() {
    var inp   = document.getElementById('code-input');
    var err   = document.getElementById('code-error');
    var code  = (inp.value || '').trim().toUpperCase();
    err.style.display = 'none';
    inp.style.borderColor = '';
    if (!code || code.length < 5) {
      inp.style.borderColor = '#ef4444';
      err.style.display = 'block';
      return;
    }
    if (tg && tg.sendData) {
      tg.sendData(JSON.stringify({ type: 'activate_code', code: code }));
    }
  });

  // Uppercase input auto
  var codeInp = document.getElementById('code-input');
  if (codeInp) {
    codeInp.addEventListener('input', function() {
      var pos = this.selectionStart;
      this.value = this.value.toUpperCase();
      this.setSelectionRange(pos, pos);
    });
  }
}

/* ════════════════════════════════
   DAILY OPTIMIZE
════════════════════════════════ */
function checkDailyOptimize() {
  var last  = localStorage.getItem(OPTIMIZE_KEY);
  var today = new Date().toDateString();
  if (last === today) return;

  document.getElementById('optimize-screen').style.display = 'flex';
  document.getElementById('main-screen').style.display     = 'none';

  var progress = 0, stepIdx = 0;
  var steps = ['Wczytywanie danych...','Kalibracja algorytmu...','Analiza wzorców...','Optymalizacja predykcji...','Finalizacja modelu...'];
  var bar   = document.getElementById('progress-bar');
  var label = document.getElementById('progress-label');
  var p     = document.querySelector('.optimize-box p');

  var interval = setInterval(function() {
    progress += 1 + Math.random() * 3;
    if (progress >= 100) progress = 100;
    bar.style.width   = progress + '%';
    label.textContent = Math.floor(progress) + '%';
    if (progress >= (stepIdx + 1) * 20 && stepIdx < steps.length - 1) {
      p.textContent = steps[++stepIdx];
    }
    if (progress >= 100) {
      clearInterval(interval);
      localStorage.setItem(OPTIMIZE_KEY, today);
      document.getElementById('algo-badge').textContent = '✅ Algo: zaktualizowany';
      setTimeout(function() {
        document.getElementById('optimize-screen').style.display = 'none';
        document.getElementById('main-screen').style.display     = 'flex';
      }, 800);
    }
  }, 60);
}

/* ════════════════════════════════
   HELPER: addBtn
════════════════════════════════ */
function addBtn(id, fn) {
  var el = document.getElementById(id);
  if (!el) return;
  var _busy = false;
  el.addEventListener('touchstart', function(e) {
    e.preventDefault();
    if (_busy) return;
    _busy = true;
    setTimeout(function() { _busy = false; }, 600);
    fn();
  }, { passive: false });
  el.addEventListener('click', function() {
    if (_busy) return;
    fn();
  });
}

/* ════════════════════════════════
   INIT
════════════════════════════════ */
if (!_uid) {
  // Gate screen — кнопка закрытия
  addBtn('gate-close-btn', function() { if (tg) tg.close(); });

} else {
  // Полная инициализация
  initGrid();
  initGoal();
  updateVavadaDisplay();
  syncDifficulty();
  document.getElementById('difficulty').addEventListener('input', syncDifficulty);

  document.getElementById('page-mines').style.display   = 'flex';
  document.getElementById('page-penalty').style.display = 'none';
  document.getElementById('page-sub').style.display     = 'none';

  addBtn('tab-mines',      function() { switchTab('mines'); });
  addBtn('tab-penalty',    function() { switchTab('penalty'); });
  addBtn('tab-sub',        function() { switchTab('sub'); });
  addBtn('signal-btn',     getSignal);
  addBtn('pen-signal-btn', getPenaltySignal);

  initSubTab();
  checkDailyOptimize();
}
