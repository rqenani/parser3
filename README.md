# Payroll Analyzer (API + UI)

Frontend minimal (index.html) thërret API:
- POST /api/parse  {type, text} -> {meta, people[]}
- POST /api/analyze {type, meta, people, selectedIndex?} -> {slideTitle, accountingHTML, personFizikHTML?, tablePunonjesHTML?, bankData[], fullData[]}

## Lokalisht
```
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.server:app --host 0.0.0.0 --port 8000
```
Hapni: http://localhost:8000

## Heroku
```
git init
git add .
git commit -m "init"
heroku create emri-yt
heroku buildpacks:set heroku/python
git push heroku HEAD:main
heroku ps:scale web=1
heroku open
```

## Railway
- Deploy from GitHub (Dockerfile) ose direkt Dockerfile.
- Healthcheck: /api/health

## Siguri
- Header-a CSP, X-Frame-Options, nosniff, etj. aktivë.
- Parsing & llogaritjet janë në backend (kod i mbrojtur).
