name: Sonic & BTC – monitor i raport

on:
  schedule:
    - cron: '7 * * * *'        # co godzinę, 7 minut po pełnej (mniejszy tłok na serwerach GitHuba)
  workflow_dispatch:
    inputs:
      force_report:
        description: 'Wygeneruj raport HTML teraz (nawet jeśli dziś już był)'
        type: boolean
        default: true

concurrency:                    # dwa przebiegi nigdy nie piszą na FTP jednocześnie
  group: handel-bot
  cancel-in-progress: false

jobs:
  run-bot:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run bot
        env:
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          FTP_HOST: ${{ secrets.FTP_HOST }}
          FTP_USER: ${{ secrets.FTP_USER }}
          FTP_PASS: ${{ secrets.FTP_PASS }}
          FORCE_REPORT: ${{ github.event_name == 'workflow_dispatch' && inputs.force_report && '1' || '0' }}
          PYTHONIOENCODING: "utf-8"
          LANG: "C.UTF-8"
          LC_ALL: "C.UTF-8"
        run: python bot.py
