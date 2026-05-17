import os, json, threading, time, csv, io
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from sqlalchemy.orm import joinedload
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import requests
from flask_compress import Compress

# ══════════════════════════════════════
#  APP & DB
# ══════════════════════════════════════
app = Flask(__name__, template_folder='.')
app.secret_key = os.environ.get("SECRET_KEY", "gbp-analyzer-secret-2025")
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
_db_url = os.environ.get("DATABASE_URL", "")
if _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = _db_url or "sqlite:///" + os.path.join(BASE_DIR, "gbp_analyzer.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["COMPRESS_MIMETYPES"] = [
    "text/html", "text/css", "application/javascript",
    "application/json", "text/plain"
]
app.config["COMPRESS_LEVEL"] = 6   # 1-9, balanço velocidade/compressão
app.config["COMPRESS_MIN_SIZE"] = 500  # só comprime > 500 bytes
db = SQLAlchemy(app)
Compress(app)   # gzip automático em todas as respostas

SKEY = os.environ.get("SERP_API_KEY", "")  # chave configurada no Render > Environment

# ══════════════════════════════════════
#  MODELS
# ══════════════════════════════════════
class User(db.Model):
    id     = db.Column(db.Integer, primary_key=True)
    nome   = db.Column(db.String(100), nullable=False)
    email  = db.Column(db.String(120), unique=True, nullable=False)
    senha  = db.Column(db.String(200), nullable=False)
    criado = db.Column(db.DateTime, default=datetime.utcnow)

class Negocio(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    nome         = db.Column(db.String(200), nullable=False)
    categoria    = db.Column(db.String(100), default="Geral")
    cidade       = db.Column(db.String(100), default="Brasília")
    wa           = db.Column(db.String(50), default="")
    cid          = db.Column(db.String(30), default="")
    r_base       = db.Column(db.Float, default=0.0)
    a_base       = db.Column(db.Integer, default=0)
    ativo        = db.Column(db.Boolean, default=True)
    criado       = db.Column(db.DateTime, default=datetime.utcnow)
    diagnosticos = db.relationship("Diagnostico", backref="negocio", lazy=True,
                                   order_by="Diagnostico.data.desc()", cascade="all, delete-orphan")

class Diagnostico(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    neg_id     = db.Column(db.Integer, db.ForeignKey("negocio.id"), nullable=False)
    data       = db.Column(db.DateTime, default=datetime.utcnow)
    score      = db.Column(db.Integer, default=0)
    nota       = db.Column(db.Float, default=0.0)
    avaliacoes = db.Column(db.Integer, default=0)
    site_ok    = db.Column(db.Boolean, default=False)
    wa_ok      = db.Column(db.Boolean, default=False)
    hrs_ok     = db.Column(db.Boolean, default=False)
    desc_ok    = db.Column(db.Boolean, default=False)
    foto_ok    = db.Column(db.Boolean, default=False)
    preco      = db.Column(db.String(20), default="")
    open_state = db.Column(db.String(100), default="")
    website    = db.Column(db.String(300), default="")
    telefone   = db.Column(db.String(50), default="")
    descricao  = db.Column(db.Text, default="")
    titulo_gmb = db.Column(db.String(200), default="")
    raw_json   = db.Column(db.Text, default="")

class JobStatus(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    rodando    = db.Column(db.Boolean, default=False)
    total      = db.Column(db.Integer, default=0)
    feitos     = db.Column(db.Integer, default=0)
    atual      = db.Column(db.String(200), default="")
    iniciado   = db.Column(db.DateTime)
    finalizado = db.Column(db.DateTime)
    erros      = db.Column(db.Integer, default=0)

class GeoGridCache(db.Model):
    """Resultados do geo-grid de posicionamento local."""
    id         = db.Column(db.Integer, primary_key=True)
    neg_id     = db.Column(db.Integer, db.ForeignKey("negocio.id"), nullable=False)
    atualizado = db.Column(db.DateTime, default=datetime.utcnow)
    grid_size  = db.Column(db.Integer, default=5)
    radius_km  = db.Column(db.Float,   default=1.0)
    center_lat = db.Column(db.Float)
    center_lng = db.Column(db.Float)
    resultados = db.Column(db.Text, default="[]")  # JSON: [{lat,lng,rank}]

class ConcorrenteCache(db.Model):
    """Concorrentes reais buscados no Google via SerpAPI."""
    id         = db.Column(db.Integer, primary_key=True)
    neg_id     = db.Column(db.Integer, db.ForeignKey("negocio.id"), nullable=False)
    atualizado = db.Column(db.DateTime, default=datetime.utcnow)
    nome       = db.Column(db.String(200), default="")
    endereco   = db.Column(db.String(300), default="")
    nota       = db.Column(db.Float, default=0.0)
    avaliacoes = db.Column(db.Integer, default=0)
    score      = db.Column(db.Integer, default=0)
    is_self    = db.Column(db.Boolean, default=False)
    pos        = db.Column(db.Integer, default=0)
    gap        = db.Column(db.Integer, default=0)
    lat        = db.Column(db.Float, default=None)
    lng        = db.Column(db.Float, default=None)


# ══════════════════════════════════════
#  SERP API
# ══════════════════════════════════════
def _serp(params):
    params.update({"hl": "pt", "gl": "br", "api_key": SKEY})
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise Exception(data["error"])
    return data

def _normalize(place):
    hrs = {}
    for item in (place.get("hours") or []):
        if isinstance(item, dict) and item.get("day"):
            hrs[item["day"]] = item.get("hours", "")
    return {
        "title":           place.get("title", ""),
        "rating":          place.get("rating"),
        "reviews":         place.get("reviews"),
        "address":         place.get("address", ""),
        "phone":           place.get("phone", ""),
        "website":         place.get("website", ""),
        "open_state":      place.get("open_state", ""),
        "operating_hours": hrs if hrs else None,
        "description":     place.get("description", ""),
        "price":           place.get("price", ""),
        "thumbnail":       place.get("thumbnail", ""),
        "data_cid":        place.get("data_cid", ""),
    }

def buscar_negocio_api(neg):
    if neg.cid:
        try:
            data = _serp({"engine": "google_maps", "type": "place", "data_cid": neg.cid})
            place = data.get("place_results") or {}
            if place.get("title"):
                return _normalize(place), None
        except Exception:
            pass
    queries = [
        f"{neg.nome} {neg.cidade}",
        f"{neg.nome} {neg.cidade} {neg.categoria}",
        " ".join(neg.nome.split()[:3]) + f" {neg.cidade}",
    ]
    for q in queries:
        try:
            data = _serp({"engine": "google_maps", "type": "search", "q": q})
            results = data.get("local_results", [])
            if results:
                palavras = neg.nome.lower().split()[:2]
                for res in results[:3]:
                    titulo = (res.get("title") or "").lower()
                    if any(p in titulo for p in palavras if len(p) > 3):
                        return res, None
                return results[0], None
        except Exception:
            continue
    return None, "Não encontrado"

def calc_score(r, wa):
    nota = float(r.get("rating") or 0)
    avs  = int(r.get("reviews") or 0)
    site = bool(r.get("website")) and "instagram.com" not in str(r.get("website", "")) \
           and "facebook.com" not in str(r.get("website", ""))
    hrs  = bool(r.get("operating_hours") or r.get("open_state"))
    desc = bool(r.get("description"))
    foto = bool(r.get("thumbnail"))
    p = 0
    p += 35 if nota >= 4.7 else 28 if nota >= 4.5 else 20 if nota >= 4.3 else 10
    p += 20 if avs >= 2000 else 15 if avs >= 500 else 9 if avs >= 100 else 3
    if site: p += 15
    if wa:   p += 10
    if hrs:  p += 10
    if desc: p += 5
    if foto: p += 5
    return min(p, 100), site, hrs, desc, foto

def _salvar_diag(neg, r):
    score, site_ok, hrs_ok, desc_ok, foto_ok = calc_score(r, neg.wa)
    d = Diagnostico(
        neg_id     = neg.id,
        score      = score,
        nota       = float(r.get("rating") or neg.r_base or 0),
        avaliacoes = int(r.get("reviews") or neg.a_base or 0),
        site_ok    = site_ok,
        wa_ok      = bool(neg.wa),
        hrs_ok     = hrs_ok,
        desc_ok    = desc_ok,
        foto_ok    = foto_ok,
        preco      = r.get("price", ""),
        open_state = r.get("open_state", ""),
        website    = r.get("website", ""),
        telefone   = r.get("phone", ""),
        descricao  = r.get("description", ""),
        titulo_gmb = r.get("title", ""),
        raw_json   = json.dumps(r, ensure_ascii=False),
    )
    if r.get("data_cid") and not neg.cid:
        neg.cid = r["data_cid"]
    db.session.add(d)
    db.session.commit()

# ══════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════
def score_class(s):
    if s is None: return "idle"
    if s >= 75:   return "ok"
    if s >= 50:   return "warn"
    return "bad"

app.jinja_env.globals["score_class"] = score_class

# ══════════════════════════════════════
#  AUTH
# ══════════════════════════════════════
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/ping")
def ping():
    """Endpoint para UptimeRobot manter o app acordado."""
    return "ok", 200

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        user  = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.senha, senha):
            session["user_id"]   = user.id
            session["user_nome"] = user.nome
            return redirect(url_for("dashboard"))
        flash("E-mail ou senha incorretos.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ══════════════════════════════════════
#  DASHBOARD
# ══════════════════════════════════════
@app.route("/")
@login_required
def dashboard():
    cat_filter    = request.args.get("cat", "")
    cidade_filter = request.args.get("cidade", "")
    negocios      = Negocio.query.filter_by(ativo=True).order_by(Negocio.categoria, Negocio.nome).all()
    job           = JobStatus.query.first()
    categorias    = sorted(set(n.categoria for n in negocios if n.categoria))
    cidades       = sorted(set(n.cidade    for n in negocios if n.cidade))
    neg_data = []
    for n in negocios:
        if cat_filter    and n.categoria != cat_filter:    continue
        if cidade_filter and n.cidade    != cidade_filter: continue
        ultimo = n.diagnosticos[0] if n.diagnosticos else None
        neg_data.append({"neg": n, "ultimo": ultimo})
    com_diag    = [d for d in neg_data if d["ultimo"]]
    criticas    = sum(1 for d in com_diag if d["ultimo"].score < 50)
    sem_site    = sum(1 for d in com_diag if not d["ultimo"].site_ok)
    media_score = round(sum(d["ultimo"].score for d in com_diag) / len(com_diag)) if com_diag else 0
    return render_template("dashboard.html",
        neg_data=neg_data, job=job, categorias=categorias, cidades=cidades,
        cat_filter=cat_filter, cidade_filter=cidade_filter, criticas=criticas,
        sem_site=sem_site, media_score=media_score,
        total=len(negocios), analisadas=len(com_diag)
    )

# ══════════════════════════════════════
#  BUSCA
# ══════════════════════════════════════
@app.route("/api/buscar")
@login_required
def api_buscar():
    q      = request.args.get("q", "").strip()
    cidade = request.args.get("cidade", "Brasília").strip()
    if not q:
        return jsonify([])
    try:
        data    = _serp({"engine": "google_maps", "type": "search", "q": f"{q} {cidade}"})
        results = data.get("local_results", [])[:8]
        out = []
        for r in results:
            out.append({
                "titulo":   r.get("title", ""),
                "endereco": r.get("address", ""),
                "nota":     r.get("rating", ""),
                "reviews":  r.get("reviews", 0),
                "tipo":     r.get("type", ""),
                "cid":      str(r.get("data_cid", "")),
                "place_id": r.get("place_id", ""),
            })
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════
#  CRUD NEGÓCIOS
# ══════════════════════════════════════
@app.route("/negocio/adicionar", methods=["POST"])
@login_required
def adicionar_negocio():
    data = request.get_json() or request.form
    neg  = Negocio(
        nome      = data.get("nome", "").strip(),
        categoria = data.get("categoria", "Geral").strip(),
        cidade    = data.get("cidade", "Brasília").strip(),
        wa        = data.get("wa", "").strip(),
        cid       = data.get("cid", "").strip(),
        r_base    = float(data.get("nota", 0) or 0),
        a_base    = int(data.get("reviews", 0) or 0),
    )
    db.session.add(neg)
    db.session.commit()
    return jsonify({"ok": True, "id": neg.id, "nome": neg.nome})

@app.route("/negocio/<int:neg_id>/editar", methods=["POST"])
@login_required
def editar_negocio(neg_id):
    neg = Negocio.query.get_or_404(neg_id)
    neg.nome      = request.form.get("nome", neg.nome).strip()
    neg.categoria = request.form.get("categoria", neg.categoria).strip()
    neg.cidade    = request.form.get("cidade", neg.cidade).strip()
    neg.wa        = request.form.get("wa", neg.wa).strip()
    db.session.commit()
    return redirect(url_for("negocio", neg_id=neg_id))

@app.route("/negocio/<int:neg_id>/remover", methods=["POST"])
@login_required
def remover_negocio(neg_id):
    neg = Negocio.query.get_or_404(neg_id)
    neg.ativo = False
    db.session.commit()
    return jsonify({"ok": True})

# ══════════════════════════════════════
#  IMPORTAÇÃO CSV
# ══════════════════════════════════════
@app.route("/importar-csv", methods=["POST"])
@login_required
def importar_csv():
    import re as _re
    f = request.files.get("arquivo")
    if not f:
        return jsonify({"ok": False, "msg": "Nenhum arquivo enviado"})
    try:
        stream = io.StringIO(f.stream.read().decode("utf-8-sig"), newline=None)
        reader = csv.DictReader(stream)
        reader.fieldnames = [c.strip() for c in (reader.fieldnames or [])]
        def _cidade(end):
            m = _re.search(r',\s*([^,\-]+?)\s*-\s*[A-Z]{2},', str(end or ''))
            return m.group(1).strip() if m else 'Brasil'
        def _cid(url):
            m = _re.search(r'cid=(\d+)', str(url or ''))
            return m.group(1) if m else ''
        def _wa(val):
            v = str(val or '')
            if v in ('nan', 'NÃO ENCONTRADO', '#ERROR!', ''): return ''
            return _re.sub(r'\D', '', v)
        def _nota(val):
            try: return float(str(val).replace(',', '.'))
            except: return 0.0
        def _avs(val):
            try: return int(val)
            except: return 0
        adicionados = 0
        for row in reader:
            nome = (row.get('Nome') or row.get('nome') or '').strip()
            if not nome: continue
            neg = Negocio(
                nome      = nome,
                categoria = (row.get('Categoria') or row.get('categoria') or 'Geral').strip(),
                cidade    = _cidade(row.get('Endereço') or row.get('Endereco') or row.get('cidade') or ''),
                wa        = _wa(row.get('Whatsapp') or row.get('whatsapp') or row.get('wa') or ''),
                cid       = _cid(row.get('URL Google Maps') or row.get('cid') or ''),
                r_base    = _nota(row.get('Nota') or row.get('nota') or 0),
                a_base    = _avs(row.get('Avaliações') or row.get('Avaliacoes') or 0),
            )
            db.session.add(neg)
            adicionados += 1
        db.session.commit()
        return jsonify({"ok": True, "adicionados": adicionados})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

# ══════════════════════════════════════
#  DIAGNÓSTICO
# ══════════════════════════════════════
_job_lock   = threading.Lock()
_grid_jobs  = {}  # neg_id -> {running, done, total, error}

@app.route("/analisar", methods=["POST"])
@login_required
def analisar_todas():
    job = JobStatus.query.first()
    if job and job.rodando:
        return jsonify({"ok": False, "msg": "Análise já em execução"})
    if not job:
        job = JobStatus()
        db.session.add(job)
    total = Negocio.query.filter_by(ativo=True).count()
    job.rodando = True; job.total = total; job.feitos = 0
    job.erros = 0; job.iniciado = datetime.utcnow()
    job.finalizado = None; job.atual = "Iniciando…"
    db.session.commit()
    threading.Thread(target=_run_all, daemon=True).start()
    return jsonify({"ok": True})

def _run_all():
    with app.app_context():
        negocios = Negocio.query.filter_by(ativo=True).all()
        job      = JobStatus.query.first()
        for i, neg in enumerate(negocios):
            with _job_lock:
                job.atual = neg.nome; job.feitos = i
                db.session.commit()
            try:
                r, err = buscar_negocio_api(neg)
                if r:
                    _salvar_diag(neg, r)
                else:
                    job.erros += 1
            except Exception:
                job.erros += 1
            time.sleep(0.8)
        job.rodando = False; job.feitos = len(negocios)
        job.atual = "Concluído"; job.finalizado = datetime.utcnow()
        db.session.commit()

@app.route("/analisar/<int:neg_id>", methods=["POST"])
@login_required
def analisar_um(neg_id):
    neg = Negocio.query.get_or_404(neg_id)
    try:
        r, err = buscar_negocio_api(neg)
        if r:
            _salvar_diag(neg, r)
            return jsonify({"ok": True})
        return jsonify({"ok": False, "msg": err})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route("/job-status")
@login_required
def job_status():
    job = JobStatus.query.first()
    if not job:
        return jsonify({"rodando": False, "feitos": 0, "total": 0, "atual": "", "erros": 0, "pct": 0})
    pct = int(job.feitos / job.total * 100) if job.total else 0
    return jsonify({
        "rodando":    job.rodando,
        "feitos":     job.feitos,
        "total":      job.total,
        "atual":      job.atual,
        "erros":      job.erros,
        "pct":        pct,
        "finalizado": job.finalizado.strftime("%d/%m %H:%M") if job.finalizado else None,
    })

@app.route("/negocio/<int:neg_id>/concorrentes", methods=["POST"])
@login_required
def buscar_concorrentes_route(neg_id):
    """Busca concorrentes reais do Google e salva no cache."""
    neg    = Negocio.query.get_or_404(neg_id)
    ultimo = neg.diagnosticos[0] if neg.diagnosticos else None
    try:
        resultado = _buscar_concorrentes_google(neg, ultimo)
        return jsonify({"ok": True, "total": len(resultado)})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})



# ══════════════════════════════════════
#  GEO-GRID
# ══════════════════════════════════════
import math as _math


def _geocode_cidade(cidade):
    """Geocodifica a cidade via Nominatim (OpenStreetMap, gratuito, sem API key)."""
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{cidade}, Brasil", "format": "json", "limit": 1},
            headers={"User-Agent": "KoffeeGBPAnalyzer/1.0 (koffeemarketing.com.br)"},
            timeout=6,
        )
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    return None, None

def _grid_points(clat, clng, grid_size, radius_km):
    """Gera os N×N pontos do geo-grid ao redor do centro."""
    half  = grid_size // 2
    d_lat = radius_km / 111.0
    d_lng = radius_km / (111.0 * _math.cos(_math.radians(clat)))
    pts   = []
    for row in range(grid_size):
        for col in range(grid_size):
            pts.append({
                "lat":  round(clat + (half - row) * d_lat / max(half, 1), 6),
                "lng":  round(clng + (col - half) * d_lng / max(half, 1), 6),
                "rank": None,
            })
    return pts

def _rank_em_ponto(neg, lat, lng):
    """Busca o negócio no Google Maps a partir de um ponto e retorna a posição."""
    try:
        data    = _serp({
            "engine": "google_maps",
            "type":   "search",
            "q":      neg.categoria,
            "ll":     f"@{lat},{lng},14z",
        })
        results = data.get("local_results", [])
        city_words = set(_norm_word(w) for w in neg.cidade.split() if len(w) >= 3)
        self_words = set(
            _norm_word(w) for w in neg.nome.split()
            if len(w) >= 3 and _norm_word(w) not in _STOP
            and _norm_word(w) not in city_words
        )
        for i, r in enumerate(results[:20]):
            cid_match = neg.cid and str(r.get("data_cid","")) == str(neg.cid)
            gw = set(_norm_word(w) for w in r.get("title","").split()
                     if len(w) >= 3 and _norm_word(w) not in _STOP)
            name_match = bool(self_words & gw) and len(self_words) > 0
            if cid_match or name_match:
                return i + 1
        return 20  # não encontrado no top 20
    except Exception:
        return None

def _run_geogrid(neg_id, grid_size, radius_km, clat, clng):
    """Thread: executa o geo-grid e salva no banco."""
    with app.app_context():
        neg   = Negocio.query.get(neg_id)
        pts   = _grid_points(clat, clng, grid_size, radius_km)
        total = len(pts)
        _grid_jobs[neg_id] = {"running": True, "done": 0, "total": total, "error": None}

        for i, pt in enumerate(pts):
            rank       = _rank_em_ponto(neg, pt["lat"], pt["lng"])
            pt["rank"] = rank
            _grid_jobs[neg_id]["done"] = i + 1
            time.sleep(0.5)  # respeita rate limit SerpAPI

        # Salva resultado
        GeoGridCache.query.filter_by(neg_id=neg_id).delete()
        gc = GeoGridCache(
            neg_id     = neg_id,
            grid_size  = grid_size,
            radius_km  = radius_km,
            center_lat = clat,
            center_lng = clng,
            resultados = json.dumps(pts),
        )
        db.session.add(gc)
        db.session.commit()
        _grid_jobs[neg_id]["running"] = False

@app.route("/negocio/<int:neg_id>/geogrid", methods=["POST"])
@login_required
def gerar_geogrid(neg_id):
    neg = Negocio.query.get_or_404(neg_id)
    if _grid_jobs.get(neg_id, {}).get("running"):
        return jsonify({"ok": False, "msg": "Geo-grid já em execução."})

    grid_size = int((request.get_json() or {}).get("grid_size", 5))
    radius_km = float((request.get_json() or {}).get("radius_km", 1.0))
    grid_size = max(3, min(7, grid_size))  # limita entre 3 e 7

    # Coordenadas: tenta self → qualquer concorrente → geocodifica a cidade
    self_cache = ConcorrenteCache.query.filter_by(neg_id=neg_id, is_self=True).first()
    any_coords = ConcorrenteCache.query.filter_by(neg_id=neg_id)                    .filter(ConcorrenteCache.lat.isnot(None)).first()

    if self_cache and self_cache.lat:
        clat, clng = self_cache.lat, self_cache.lng
    elif any_coords:
        clat, clng = any_coords.lat, any_coords.lng
    else:
        # Geocodifica a cidade via Nominatim (sem créditos, sem API key)
        clat, clng = _geocode_cidade(neg.cidade)
        if not clat:
            return jsonify({"ok": False, "msg": f"Não foi possível localizar '{neg.cidade}'. Verifique o nome da cidade do negócio."})
    total = grid_size ** 2
    threading.Thread(
        target=_run_geogrid,
        args=(neg_id, grid_size, radius_km, clat, clng),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "total": total})

@app.route("/negocio/<int:neg_id>/geogrid/status")
@login_required
def geogrid_status(neg_id):
    job = _grid_jobs.get(neg_id)
    if not job:
        # Verifica se já tem resultado salvo
        gc = GeoGridCache.query.filter_by(neg_id=neg_id).first()
        if gc:
            return jsonify({"running": False, "done": gc.grid_size**2,
                            "total": gc.grid_size**2, "pronto": True})
        return jsonify({"running": False, "done": 0, "total": 0, "pronto": False})
    pct = int(job["done"] / job["total"] * 100) if job["total"] else 0
    return jsonify({**job, "pct": pct, "pronto": not job["running"]})

@app.route("/negocio/<int:neg_id>/geogrid/dados")
@login_required
def geogrid_dados(neg_id):
    gc = GeoGridCache.query.filter_by(neg_id=neg_id).first()
    if not gc:
        return jsonify({"ok": False})
    return jsonify({
        "ok":         True,
        "grid_size":  gc.grid_size,
        "radius_km":  gc.radius_km,
        "center_lat": gc.center_lat,
        "center_lng": gc.center_lng,
        "resultados": json.loads(gc.resultados),
        "atualizado": gc.atualizado.strftime("%d/%m/%Y %H:%M"),
    })

# ══════════════════════════════════════
#  DETALHE DO NEGÓCIO — helpers
# ══════════════════════════════════════
def _get_criterios(neg, d):
    """20 critérios derivados dos dados do banco."""
    if not d:
        return [], 0, 0, 0
    nota = float(d.nota or 0)
    avs  = int(d.avaliacoes or 0)
    def st(cond_ok, cond_warn=False):
        if cond_ok:   return "ok"
        if cond_warn: return "warn"
        return "bad"
    def nota_label(n):
        if n >= 4.5: return "Excelente"
        if n >= 4.0: return "Regular — meta: 4.5+"
        return "Crítica — prioridade máxima"
    def avs_label(a):
        if a >= 500: return "Excelente"
        if a >= 100: return "Bom"
        return "Baixo — estratégia de captação necessária"
    criterios = [
        {"g":"Informações básicas","n":"Nome do negócio",          "s":"ok",                         "i":"Nome cadastrado e verificado no perfil"},
        {"g":"Informações básicas","n":"Endereço e localização",   "s":"ok",                         "i":"Endereço verificado, pin correto no Google Maps"},
        {"g":"Informações básicas","n":"Telefone de contato",      "s":st(bool(d.telefone)),         "i":d.telefone or "Telefone não encontrado no perfil"},
        {"g":"Informações básicas","n":"Site próprio vinculado",   "s":st(d.site_ok),               "i":(d.website[:45]+"…") if d.site_ok and d.website else "Nenhum site vinculado ao perfil"},
        {"g":"Informações básicas","n":"WhatsApp Business",        "s":st(bool(neg.wa)),             "i":neg.wa or "Sem WhatsApp — perde conversões diretas"},
        {"g":"Horários","n":"Horário de funcionamento",            "s":st(d.hrs_ok),                 "i":"Todos os dias preenchidos" if d.hrs_ok else "Horários não preenchidos"},
        {"g":"Horários","n":"Status aberto / fechado",             "s":st(bool(d.open_state), True), "i":d.open_state or "Status não identificado"},
        {"g":"Horários","n":"Horários especiais e feriados",       "s":"warn",                       "i":"Verificar manualmente no perfil"},
        {"g":"Fotos","n":"Foto de capa / perfil",                  "s":st(d.foto_ok),                "i":"Foto presente" if d.foto_ok else "Sem foto de capa profissional"},
        {"g":"Fotos","n":"Logo e identidade visual",               "s":"warn",                       "i":"Verificar resolução mínima (250x250px)"},
        {"g":"Fotos","n":"Fotos do interior",                      "s":"warn",                       "i":"Verificar quantidade — recomendado mínimo 10"},
        {"g":"Fotos","n":"Fotos de produtos / cardápio",           "s":"warn",                       "i":"Verificar — impacta diretamente o ranqueamento"},
        {"g":"Fotos","n":"Fotos do exterior / fachada",            "s":"warn",                       "i":"Facilita a identificação do local pelos clientes"},
        {"g":"Reputação","n":"Nota média de avaliações",           "s":st(nota>=4.5, nota>=4.0),    "i":str(nota)+"★ — "+nota_label(nota)},
        {"g":"Reputação","n":"Volume de avaliações",               "s":st(avs>=500, avs>=100),      "i":str(avs)+" avaliações — "+avs_label(avs)},
        {"g":"Reputação","n":"Taxa de resposta a avaliações",      "s":"warn",                       "i":"Verificar — Google penaliza quem ignora avaliações"},
        {"g":"Reputação","n":"Publicações (Posts GMB)",            "s":"warn",                       "i":"Verificar data do último post — recomendado 2x/semana"},
        {"g":"Completude","n":"Descrição do negócio",              "s":st(d.desc_ok),               "i":(d.descricao[:50]+"…") if d.desc_ok and d.descricao else "Campo em branco — perde palavras-chave de SEO"},
        {"g":"Completude","n":"Preço / faixa de preço",            "s":st(bool(d.preco), False),    "i":d.preco or "Preço não cadastrado no perfil"},
        {"g":"Completude","n":"Atributos e serviços",              "s":"warn",                       "i":"Verificar: Wi-fi, pagamento, acessibilidade…"},
    ]
    bons  = sum(1 for c in criterios if c["s"] == "ok")
    regs  = sum(1 for c in criterios if c["s"] == "warn")
    ruins = sum(1 for c in criterios if c["s"] == "bad")
    return criterios, bons, regs, ruins


import unicodedata as _ud

def _norm_word(w):
    """Normaliza palavra: remove acentos e converte para minúsculo."""
    return _ud.normalize('NFD', w.lower()).encode('ascii', 'ignore').decode()

# Palavras genéricas que não identificam um negócio específico
_STOP = {
    'cafe', 'coffee', 'bar', 'restaurante', 'restaurant', 'lanchonete',
    'padaria', 'bakery', 'bistro', 'botanica', 'botanic', 'cultural',
    'conceito', 'gourmet', 'artesanal', 'especial', 'especialidades',
    'torrefacao', 'mineiro', 'esplanada', 'brunch', 'negocio',
    'and', 'the', 'de', 'do', 'da', 'dos', 'das', 'em', 'no', 'na', 'e',
}

def _buscar_concorrentes_google(neg, ultimo):
    """
    Busca concorrentes reais no Google Maps via SerpAPI.
    Salva resultados em ConcorrenteCache.
    Retorna lista formatada para o template.
    """
    query = f"{neg.categoria} {neg.cidade}"
    data  = _serp({"engine": "google_maps", "type": "search", "q": query})
    results = data.get("local_results", [])[:7]

    # Apaga cache antigo deste negócio
    ConcorrenteCache.query.filter_by(neg_id=neg.id).delete()

    # Palavras significativas do nome (sem stop words, sem nome da cidade, normalizadas)
    # Exclui cidade para evitar falsos positivos em negócios que usam o nome da cidade
    city_words = set(_norm_word(w) for w in neg.cidade.split() if len(w) >= 3)
    self_words = set(
        _norm_word(w) for w in neg.nome.split()
        if len(w) >= 3
        and _norm_word(w) not in _STOP
        and _norm_word(w) not in city_words
    )

    lista = []
    self_found = False

    for r in results:
        score, site_ok, hrs_ok, desc_ok, foto_ok = calc_score(r, "")
        nome_google = r.get("title", "")
        # Identifica se é o próprio negócio (CID ou nome)
        cid_match  = neg.cid and str(r.get("data_cid", "")) == str(neg.cid)
        google_words_norm = set(
            _norm_word(w) for w in nome_google.split()
            if len(w) >= 3 and _norm_word(w) not in _STOP
        )
        name_match = bool(self_words & google_words_norm) and len(self_words) > 0
        is_self    = cid_match or name_match
        if is_self:
            self_found = True

        gps = r.get("gps_coordinates") or {}
        c = ConcorrenteCache(
            neg_id     = neg.id,
            nome       = nome_google,
            endereco   = r.get("address", ""),
            nota       = float(r.get("rating") or 0),
            avaliacoes = int(r.get("reviews") or 0),
            score      = score,
            is_self    = is_self,
            lat        = float(gps.get("latitude"))  if gps.get("latitude")  else None,
            lng        = float(gps.get("longitude")) if gps.get("longitude") else None,
        )
        db.session.add(c)
        lista.append({"nome": nome_google, "nota": float(r.get("rating") or 0),
                      "avs": int(r.get("reviews") or 0), "score": score, "is_self": is_self,
                      "cidade": r.get("address", "").split(",")[0]})

    # Se o próprio negócio não apareceu, adiciona com dados do banco
    if not self_found and ultimo:
        c = ConcorrenteCache(
            neg_id     = neg.id,
            nome       = neg.nome,
            endereco   = neg.cidade,
            nota       = ultimo.nota,
            avaliacoes = ultimo.avaliacoes,
            score      = ultimo.score,
            is_self    = True,
        )
        db.session.add(c)
        lista.append({"nome": neg.nome, "nota": ultimo.nota,
                      "avs": ultimo.avaliacoes, "score": ultimo.score,
                      "is_self": True, "cidade": neg.cidade})

    db.session.commit()

    # Ordena e calcula posição / gap
    lista.sort(key=lambda x: x["score"], reverse=True)
    lider = lista[0]["score"] if lista else 0
    for i, item in enumerate(lista):
        item["id"]  = None
        item["pos"] = i + 1
        item["gap"] = item["score"] - lider
    return lista

def _get_concorrentes(neg, ultimo):
    """Lê concorrentes do cache (Google). Retorna [] se ainda não foi buscado.
    Re-avalia is_self com a lógica corrigida para corrigir caches antigos
    sem precisar de nova chamada à API.
    """
    cached = ConcorrenteCache.query.filter_by(neg_id=neg.id)                .order_by(ConcorrenteCache.score.desc()).all()
    if not cached:
        return []

    # Recalcula is_self com lógica corrigida (exclui cidade e stop words)
    city_words = set(_norm_word(w) for w in neg.cidade.split() if len(w) >= 3)
    self_words = set(
        _norm_word(w) for w in neg.nome.split()
        if len(w) >= 3
        and _norm_word(w) not in _STOP
        and _norm_word(w) not in city_words
    )

    self_found = False
    lista = []
    for c in cached:
        # CID match (mais confiável) ou nome match com lógica corrigida
        cid_match  = neg.cid and c.nome == neg.nome  # nome exato = é o fallback
        gw = set(_norm_word(w) for w in c.nome.split()
                 if len(w) >= 3 and _norm_word(w) not in _STOP)
        name_match = bool(self_words & gw) and len(self_words) > 0
        is_self    = cid_match or name_match

        if is_self:
            self_found = True

        lista.append({
            "id":      None,
            "nome":    c.nome,
            "cidade":  c.endereco.split(",")[0] if c.endereco else neg.cidade,
            "score":   c.score,
            "nota":    c.nota,
            "avs":     c.avaliacoes,
            "is_self": is_self,
            "pos":     0,
            "gap":     0,
            "lat":     c.lat,
            "lng":     c.lng,
        })

    # Se nenhum foi identificado como self, marca o nome exato ou o de menor score
    if not self_found and ultimo:
        # Procura pelo nome exato do negócio no cache
        exact = next((item for item in lista if item["nome"] == neg.nome), None)
        if exact:
            exact["is_self"] = True
        else:
            # Adiciona o próprio negócio como entrada extra
            lista.append({
                "id": None, "nome": neg.nome, "cidade": neg.cidade,
                "score": ultimo.score, "nota": ultimo.nota,
                "avs": ultimo.avaliacoes, "is_self": True, "pos": 0, "gap": 0,
            })

    # Ordena e calcula posição / gap
    lista.sort(key=lambda x: x["score"], reverse=True)
    lider = lista[0]["score"] if lista else 0
    for i, item in enumerate(lista):
        item["pos"] = i + 1
        item["gap"] = item["score"] - lider
    return lista

# ══════════════════════════════════════
#  DETALHE DO NEGÓCIO — rota
# ══════════════════════════════════════
@app.route("/negocio/<int:neg_id>")
@login_required
def negocio(neg_id):
    neg    = Negocio.query.options(joinedload(Negocio.diagnosticos)).get_or_404(neg_id)
    diags  = neg.diagnosticos
    ultimo = diags[0] if diags else None
    historico    = list(reversed(diags[:10]))
    chart_labels = [d.data.strftime("%d/%m") for d in historico]
    chart_scores = [d.score for d in historico]
    chart_notas  = [d.nota  for d in historico]
    issues = []
    if ultimo:
        if not ultimo.wa_ok:
            issues.append({"crit":True,  "t":"Sem WhatsApp no perfil",      "d":"Clientes não têm como entrar em contato direto pelo perfil Google."})
        if not ultimo.site_ok:
            issues.append({"crit":True,  "t":"Sem site próprio vinculado",   "d":"Reduz autoridade e visibilidade nas buscas locais."})
        if not ultimo.hrs_ok:
            issues.append({"crit":True,  "t":"Horários não preenchidos",     "d":"O Google penaliza perfis incompletos no ranqueamento."})
        if not ultimo.desc_ok:
            issues.append({"crit":False, "t":"Sem descrição do negócio",     "d":"Descrições com palavras-chave melhoram o SEO local."})
        if ultimo.nota < 4.4:
            issues.append({"crit":True,  "t":f"Avaliação {ultimo.nota} — abaixo do ideal","d":"Concorrentes com 4.5+ aparecem primeiro nos resultados."})
        if ultimo.avaliacoes < 150:
            issues.append({"crit":False, "t":f"Poucas avaliações ({ultimo.avaliacoes})","d":"Estratégia de captação pode mudar isso rapidamente."})
    criterios, bons, regs, ruins = _get_criterios(neg, ultimo)
    concorrentes = _get_concorrentes(neg, ultimo)
    geogrid_obj  = GeoGridCache.query.filter_by(neg_id=neg_id).first()
    geogrid = None
    if geogrid_obj:
        geogrid = {
            "atualizado": geogrid_obj.atualizado.strftime("%d/%m/%Y %H:%M"),
            "grid_size":  geogrid_obj.grid_size,
            "radius_km":  geogrid_obj.radius_km,
        }
    return render_template("negocio.html",
        neg=neg, ultimo=ultimo, diags=diags,
        chart_labels=json.dumps(chart_labels),
        chart_scores=json.dumps(chart_scores),
        chart_notas=json.dumps(chart_notas),
        issues=issues,
        criterios=criterios, bons=bons, regs=regs, ruins=ruins,
        concorrentes=concorrentes,
        geogrid=geogrid,
    )

# ══════════════════════════════════════
#  INIT DB
# ══════════════════════════════════════
def init_db():
    db.create_all()
    # Cria tabela geogrid se não existir (compatibilidade com DBs antigos)
    try:
        db.session.execute(text("SELECT 1 FROM geo_grid_cache LIMIT 1"))
    except Exception:
        db.session.rollback()
        db.create_all()
    # Migração: adiciona colunas lat/lng se não existirem (para DBs antigos)
    from sqlalchemy import text
    for col in ("lat REAL", "lng REAL"):
        try:
            db.session.execute(text(f"ALTER TABLE concorrente_cache ADD COLUMN {col}"))
            db.session.commit()
        except Exception:
            db.session.rollback()
    if not User.query.first():
        db.session.add(User(
            nome  = "Kaio Carvalho",
            email = "kaio@koffeemarketing.com.br",
            senha = generate_password_hash("koffee2025"),
        ))
        db.session.commit()
        print("✓ Admin criado")
    if not JobStatus.query.first():
        db.session.add(JobStatus())
        db.session.commit()

with app.app_context():
    init_db()


@app.route('/template-csv')
@login_required
def template_csv():
    from flask import Response
    csv_content = "nome,categoria,cidade,whatsapp\nMeu Negocio,Categoria,Brasilia,61999999999\n"
    return Response(csv_content, mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=template_gbp.csv'})

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
