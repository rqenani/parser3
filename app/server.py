from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from typing import List, Optional, Literal
import os, re

app = FastAPI(title="Payroll Analyzer API+UI", docs_url="/docs", redoc_url=None)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        resp: Response = await call_next(request)
        resp.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return resp

app.add_middleware(SecurityHeadersMiddleware)

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(static_dir, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/api/health")
async def health():
    return {"status": "ok"}

# ----------------- Models -----------------
class Person(BaseModel):
    id: str
    emri: str
    pagaBruto: float = 0.0
    kontrShoqTotal: float = 0.0
    kontrSuplTotal: float = 0.0
    kontrShoqDhenes: float = 0.0
    kontrShoqMarres: float = 0.0
    kontrSuplDhenes: float = 0.0
    kontrSuplMarres: float = 0.0
    kontrShendetTotal: float = 0.0
    pensionVullnetar: float = 0.0
    tap: float = 0.0

class Meta(BaseModel):
    emri: str = "Subjekti i Panjohur"
    muaji: str = "Muaji i Panjohur"

class ParseIn(BaseModel):
    type: Literal["PF","SHPK"]
    text: str

class ParseOut(BaseModel):
    meta: Meta
    people: List[Person]

class AnalyzeIn(BaseModel):
    type: Literal["PF","SHPK"]
    meta: Meta
    people: List[Person]
    # For PF: selectedIndex = pronari
    # For SHPK: adminIndex (can be -1 if none); kept for parity though logic uses all
    selectedIndex: Optional[int] = None
    adminIndex: Optional[int] = None

class AnalyzeOut(BaseModel):
    slideTitle: str
    accountingHTML: str
    personFizikHTML: Optional[str] = None
    tablePunonjesHTML: Optional[str] = None
    bankData: List[dict]
    fullData: List[dict]

# --------------- Helpers -----------------
def safe_float(val: str) -> float:
    try:
        return float(val.replace(",", ""))
    except:
        return 0.0

def parse_text(t: str) -> tuple[Meta, list[Person]]:
    text = t or ""
    emri_m = re.search(r"Emri i Tatimpaguesit:\s*([\w\s]+?)\s*3\)", text)
    muaji_m = re.search(r"Muaji:\s*(\w+)", text)
    meta = Meta(
        emri=emri_m.group(1).strip() if emri_m else "Subjekti i Panjohur",
        muaji=muaji_m.group(1).strip() if muaji_m else "Muaji i Panjohur"
    )
    cleaned = re.sub(r"(\r\n|\n|\r)", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Each person starts with: [nr rendor] [10-char id] then content until next such pattern or "Totali i Listepageses"
    person_regex = re.compile(r"(\d+)\s+(\w{10})\s+(.*?)(?=\s\d+\s+\w{10}\s+|\s+Totali i Listepageses)", re.IGNORECASE)
    people: list[Person] = []
    for m in person_regex.finditer(cleaned):
        pid = m.group(2).strip()
        datablock = m.group(3).strip()
        parts = datablock.split()
        name_parts, numbers, found_first_num = [], [], False
        for part in parts:
            if not found_first_num and re.match(r"^\d", part):
                found_first_num = True
            if found_first_num:
                numbers.append(part)
            else:
                name_parts.append(part)
        name = " ".join(name_parts).strip()
        # Expect at least ~19 numeric fields as në JS
        # Guard against short lines
        def num_at(idx):
            if idx < len(numbers):
                return safe_float(numbers[idx])
            return 0.0

        p = Person(
            id=pid,
            emri=name or pid,
            pagaBruto=num_at(4),
            kontrShoqTotal=num_at(8),
            kontrSuplTotal=num_at(11),
            kontrShoqDhenes=num_at(6),
            kontrShoqMarres=num_at(7),
            kontrSuplDhenes=num_at(9),
            kontrSuplMarres=num_at(10),
            kontrShendetTotal=num_at(14),
            pensionVullnetar=num_at(15),
            tap=num_at(18),
        )
        # Heuristic from JS: if PF owner row with 0 bruto and 9200 social, set 40000
        if p.pagaBruto == 0 and p.kontrShoqTotal == 9200:
            p.pagaBruto = 40000
        people.append(p)
    return meta, people

def fmt_num(x: float, decimals: int|None=None) -> str:
    if decimals is None:
        s = f"{x:,.0f}"
    else:
        s = f"{x:,.{decimals}f}"
    # Use Albanian style: '.' thousands and ',' decimals is common in EU, but the UI previously used sq-AL.
    # Keep simple: replace ',' with space to avoid locale; or leave English. We'll keep English grouping.
    return s

def analyze_pf(meta: Meta, people: list[Person], selected_idx: int|None) -> AnalyzeOut:
    if selected_idx is None or selected_idx < 0 or selected_idx >= len(people):
        selected_idx = 0
    owner = people[selected_idx]
    workers = [p for i,p in enumerate(people) if i != selected_idx]

    totalPagaBrutoPun = 0.0
    totalKontrKompPun = 0.0
    totalTAPPun = 0.0
    totalPagaNetoPun = 0.0
    totalDetyrime431Pun = 0.0
    bankData, fullData = [], []

    rows = []
    for p in workers:
        shendet_ndarje = p.kontrShendetTotal / 2.0
        komp_shoq = p.kontrShoqDhenes + p.kontrSuplDhenes
        komp_shendet = shendet_ndarje
        pun_shoq = p.kontrShoqMarres + p.kontrSuplMarres
        pun_shendet = shendet_ndarje
        paga_neto = p.pagaBruto - pun_shoq - pun_shendet - p.tap - p.pensionVullnetar

        totalPagaBrutoPun += p.pagaBruto
        totalKontrKompPun += komp_shoq + komp_shendet
        totalTAPPun += p.tap
        totalPagaNetoPun += paga_neto
        totalDetyrime431Pun += p.kontrShoqTotal + p.kontrSuplTotal + p.kontrShendetTotal

        bankData.append({"Emri / Mbiemri": p.emri, "Paga Neto": round(paga_neto,2)})
        fullData.append({
            "ID": p.id, "Emri": p.emri, "Paga Bruto": p.pagaBruto,
            "Sig_Kompania": round(komp_shoq + komp_shendet,2),
            "Sig_Punonjesi": round(pun_shoq + pun_shendet,2),
            "TAP": p.tap, "Paga Neto": round(paga_neto,2)
        })
        rows.append(f"<tr><td>{p.id}</td><td>{p.emri}</td><td>{fmt_num(p.pagaBruto)}</td><td>{fmt_num(pun_shoq + pun_shendet,2)}</td><td>{fmt_num(p.tap)}</td><td>{fmt_num(paga_neto,2)}</td></tr>")

    kostoSigPF = owner.kontrShoqTotal + owner.kontrSuplTotal + owner.kontrShendetTotal
    totalShpenzimPage = totalPagaBrutoPun
    totalShpenzimSig = totalKontrKompPun + kostoSigPF
    totalDetyrimePersonel = totalPagaNetoPun
    totalDetyrimeSig = totalDetyrime431Pun + kostoSigPF
    totalDetyrimeTAP = totalTAPPun
    totalDebit = totalShpenzimPage + totalShpenzimSig
    totalKredit = totalDetyrimePersonel + totalDetyrimeSig + totalDetyrimeTAP

    accountingHTML = f"""
    <h3 class="section-title">Regjistrimi Kontabël</h3>
    <div class="table-wrapper"><table>
    <thead><tr><th>Llogaria</th><th>Kodi</th><th>Debit</th><th>Kredit</th></tr></thead>
    <tbody>
    <tr><td>Shpenzime Page (Punonjësit)</td><td>641</td><td>{fmt_num(totalShpenzimPage)}</td><td></td></tr>
    <tr><td>Shpenzime Sigurimesh (Biznesi)</td><td>644</td><td>{fmt_num(totalShpenzimSig,2)}</td><td></td></tr>
    <tr><td>Detyrime ndaj Personelit (Neto)</td><td>421</td><td></td><td>{fmt_num(totalDetyrimePersonel,2)}</td></tr>
    <tr><td>Detyrime për Sigurimet</td><td>431</td><td></td><td>{fmt_num(totalDetyrimeSig,2)}</td></tr>
    <tr><td>Detyrime për TAP</td><td>442</td><td></td><td>{fmt_num(totalDetyrimeTAP)}</td></tr>
    </tbody>
    <tfoot><tr><td colspan="2">TOTALI</td><td>{fmt_num(totalDebit,2)}</td><td>{fmt_num(totalKredit,2)}</td></tr></tfoot>
    </table></div>
    """
    ownerHTML = f"""
    <h3 class="section-title">Detajet për Pronarin</h3>
    <div class="table-wrapper"><table>
    <thead><tr><th>ID</th><th>Emri</th><th>Paga Bruto (Referencë)</th><th>Kosto Totale Sigurimesh</th></tr></thead>
    <tbody><tr><td>{owner.id}</td><td>{owner.emri}</td><td>{fmt_num(owner.pagaBruto)}</td><td>{fmt_num(kostoSigPF,2)}</td></tr></tbody>
    </table></div>
    """
    workersHTML = ""
    if rows:
        workersHTML = f"""
        <h3 class="section-title">Detajet për Punonjësit</h3>
        <div class="table-wrapper"><table>
        <thead><tr><th>ID</th><th>Emri</th><th>Paga Bruto</th><th>Sigurime Punonjës</th><th>TAP</th><th>Paga Neto</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
        </table></div>
        """
    return AnalyzeOut(
        slideTitle=f"Analizë PF: {meta.emri} - {meta.muaji}",
        accountingHTML=accountingHTML,
        personFizikHTML=ownerHTML,
        tablePunonjesHTML=workersHTML,
        bankData=bankData,
        fullData=fullData
    )

def analyze_shpk(meta: Meta, people: list[Person]) -> AnalyzeOut:
    totalPagaBruto = 0.0
    totalKontrKomp = 0.0
    totalTAP = 0.0
    totalPagaNeto = 0.0
    totalDetyrime431 = 0.0
    bankData, fullData = [], []
    rows = []
    for p in people:
        shendet_ndarje = p.kontrShendetTotal / 2.0
        komp_shoq = p.kontrShoqDhenes + p.kontrSuplDhenes
        komp_shendet = shendet_ndarje
        pun_shoq = p.kontrShoqMarres + p.kontrSuplMarres
        pun_shendet = shendet_ndarje
        paga_neto = p.pagaBruto - pun_shoq - pun_shendet - p.tap - p.pensionVullnetar

        totalPagaBruto += p.pagaBruto
        totalKontrKomp += komp_shoq + komp_shendet
        totalTAP += p.tap
        totalPagaNeto += paga_neto
        totalDetyrime431 += p.kontrShoqTotal + p.kontrSuplTotal + p.kontrShendetTotal

        bankData.append({"Emri / Mbiemri": p.emri, "Paga Neto": round(paga_neto,2)})
        fullData.append({
            "ID": p.id, "Emri": p.emri, "Paga Bruto": p.pagaBruto,
            "Sig_Kompania": round(komp_shoq + komp_shendet,2),
            "Sig_Punonjesi": round(pun_shoq + pun_shendet,2),
            "TAP": p.tap, "Paga Neto": round(paga_neto,2)
        })
        rows.append(f"<tr><td>{p.id}</td><td>{p.emri}</td><td>{fmt_num(p.pagaBruto)}</td><td>{fmt_num(pun_shoq + pun_shendet,2)}</td><td>{fmt_num(p.tap)}</td><td>{fmt_num(paga_neto,2)}</td></tr>")

    totalDebit = totalPagaBruto + totalKontrKomp
    totalKredit = totalPagaNeto + totalDetyrime431 + totalTAP

    accountingHTML = f"""
    <h3 class="section-title">Regjistrimi Kontabël</h3>
    <div class="table-wrapper"><table>
    <thead><tr><th>Llogaria</th><th>Kodi</th><th>Debit</th><th>Kredit</th></tr></thead>
    <tbody>
    <tr><td>Shpenzime Page</td><td>641</td><td>{fmt_num(totalPagaBruto)}</td><td></td></tr>
    <tr><td>Shpenzime Sigurimesh</td><td>644</td><td>{fmt_num(totalKontrKomp,2)}</td><td></td></tr>
    <tr><td>Detyrime ndaj Personelit (Neto)</td><td>421</td><td></td><td>{fmt_num(totalPagaNeto,2)}</td></tr>
    <tr><td>Detyrime për Sigurimet</td><td>431</td><td></td><td>{fmt_num(totalDetyrime431,2)}</td></tr>
    <tr><td>Detyrime për TAP</td><td>442</td><td></td><td>{fmt_num(totalTAP)}</td></tr>
    </tbody>
    <tfoot><tr><td colspan="2">TOTALI</td><td>{fmt_num(totalDebit,2)}</td><td>{fmt_num(totalKredit,2)}</td></tr></tfoot>
    </table></div>
    """
    workersHTML = f"""
    <h3 class="section-title">Detajet për Punonjësit</h3>
    <div class="table-wrapper"><table>
    <thead><tr><th>ID</th><th>Emri</th><th>Paga Bruto</th><th>Sigurime Punonjës</th><th>TAP</th><th>Paga Neto</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
    </table></div>
    """
    return AnalyzeOut(
        slideTitle=f"Analizë SHPK: {meta.emri} - {meta.muaji}",
        accountingHTML=accountingHTML,
        tablePunonjesHTML=workersHTML,
        personFizikHTML=None,
        bankData=bankData,
        fullData=fullData
    )

# ----------------- Endpoints -----------------
@app.post("/api/parse", response_model=ParseOut)
async def api_parse(inp: ParseIn):
    meta, people = parse_text(inp.text or "")
    return ParseOut(meta=meta, people=people)

@app.post("/api/analyze", response_model=AnalyzeOut)
async def api_analyze(inp: AnalyzeIn):
    if inp.type == "PF":
        return analyze_pf(inp.meta, inp.people, inp.selectedIndex if inp.selectedIndex is not None else 0)
    else:
        return analyze_shpk(inp.meta, inp.people)
