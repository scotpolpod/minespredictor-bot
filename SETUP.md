# MinesPredictor — Instrukcja uruchomienia

## 1. Utwórz bota w Telegram
1. Napisz do @BotFather → /newbot
2. Podaj nazwę: `MinesPredictor`
3. Podaj username: `MinesPredictorBot` (lub podobny)
4. Skopiuj token i wklej do `.env` → `BOT_TOKEN=...`

## 2. Opublikuj webapp
Opcja A — **GitHub Pages** (darmowe):
1. Utwórz repozytorium na GitHub
2. Wrzuć pliki z folderu `webapp/`
3. Włącz GitHub Pages (Settings → Pages → main branch)
4. Wklej URL do `.env` → `WEBAPP_URL=https://twój-login.github.io/repo/index.html`

Opcja B — **Vercel** (darmowe):
```bash
npm i -g vercel
cd webapp
vercel --prod
```

Opcja C — **ngrok** (do testów lokalnych):
```bash
cd webapp
python3 -m http.server 8080
# w drugim terminalu:
ngrok http 8080
```

## 3. Ustaw WebApp w BotFather
1. @BotFather → /mybots → wybierz bota
2. Bot Settings → Menu Button → Set URL
3. Wklej URL z `.env`

## 4. Uruchom bota
```bash
cd MinesPredictor
pip install -r requirements.txt
python bot.py
```

## Struktura plików
```
MinesPredictor/
├── bot.py           # Telegram bot (aiogram)
├── requirements.txt
├── .env             # Token i URL (nie wrzucaj na GitHub!)
├── SETUP.md
└── webapp/
    ├── index.html   # Mini-app
    ├── style.css    # Style
    └── app.js       # Logika
```
