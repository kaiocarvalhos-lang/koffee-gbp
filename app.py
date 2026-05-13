import os, json, threading, time, csv, io
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import requests

# ══════════════════════════════════════
#  APP & DB
# ══════════════════════════════════════
app = Flask(__name__, template_folder='.')
app.secret_key = os.environ.get("SECRET_KEY", "gbp-analyzer-secret-2025")
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "gbp_analyzer.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

SKEY = os.environ.get("SERP_API_KEY", "05746604c702ad7a4456cbbf34ae1e356f6ed6b146a5f85f0ac9cdfb8a71f15e")

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
    # Camada 1: CID direto
    if neg.cid:
        try:
            data = _serp({"engine": "google_maps", "type": "place", "data_cid": neg.cid})
            place = data.get("place_results") or {}
            if place.get("title"):
                return _normalize(place), None
        except Exception:
            pass
    # Camada 2: busca por nome + cidade
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
                return results[0], None  # fallback: primeiro resultado
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
    # atualiza CID se ainda não tinha
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
    cat_filter = request.args.get("cat", "")
    negocios   = Negocio.query.filter_by(ativo=True).order_by(Negocio.categoria, Negocio.nome).all()
    job        = JobStatus.query.first()
    categorias = sorted(set(n.categoria for n in negocios if n.categoria))

    neg_data = []
    for n in negocios:
        if cat_filter and n.categoria != cat_filter:
            continue
        ultimo = n.diagnosticos[0] if n.diagnosticos else None
        neg_data.append({"neg": n, "ultimo": ultimo})

    com_diag    = [d for d in neg_data if d["ultimo"]]
    criticas    = sum(1 for d in com_diag if d["ultimo"].score < 50)
    sem_site    = sum(1 for d in com_diag if not d["ultimo"].site_ok)
    media_score = round(sum(d["ultimo"].score for d in com_diag) / len(com_diag)) if com_diag else 0

    return render_template("dashboard.html",
        neg_data=neg_data, job=job, categorias=categorias,
        cat_filter=cat_filter, criticas=criticas,
        sem_site=sem_site, media_score=media_score,
        total=len(negocios), analisadas=len(com_diag)
    )

# ══════════════════════════════════════
#  BUSCA (para o modal)
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

@app.route("/negocio/<int:neg_id>/editar", methods=["GET", "POST"])
@login_required
def editar_negocio(neg_id):
    neg = Negocio.query.get_or_404(neg_id)
    if request.method == "POST":
        neg.nome      = request.form.get("nome", neg.nome).strip()
        neg.categoria = request.form.get("categoria", neg.categoria).strip()
        neg.cidade    = request.form.get("cidade", neg.cidade).strip()
        neg.wa        = request.form.get("wa", neg.wa).strip()
        db.session.commit()
        flash("Negócio atualizado.", "ok")
        return redirect(url_for("negocio", neg_id=neg_id))
    return render_template("editar.html", neg=neg)

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
    f = request.files.get("arquivo")
    if not f:
        return jsonify({"ok": False, "msg": "Nenhum arquivo enviado"})
    try:
        stream  = io.StringIO(f.stream.read().decode("utf-8-sig"), newline=None)
        reader  = csv.DictReader(stream)
        adicionados = 0
        erros = []
        for row in reader:
            nome = (row.get("nome") or row.get("Nome") or "").strip()
            if not nome:
                continue
            neg = Negocio(
                nome      = nome,
                categoria = (row.get("categoria") or row.get("Categoria") or "Geral").strip(),
                cidade    = (row.get("cidade") or row.get("Cidade") or "Brasília").strip(),
                wa        = (row.get("whatsapp") or row.get("WhatsApp") or row.get("wa") or "").strip(),
                cid       = (row.get("cid") or row.get("CID") or "").strip(),
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
_job_lock = threading.Lock()

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

# ══════════════════════════════════════
#  DETALHE DO NEGÓCIO
# ══════════════════════════════════════
@app.route("/negocio/<int:neg_id>")
@login_required
def negocio(neg_id):
    neg    = Negocio.query.get_or_404(neg_id)
    diags  = neg.diagnosticos
    ultimo = diags[0] if diags else None

    historico    = list(reversed(diags[:10]))
    chart_labels = [d.data.strftime("%d/%m") for d in historico]
    chart_scores = [d.score for d in historico]
    chart_notas  = [d.nota  for d in historico]

    issues = []
    if ultimo:
        if not ultimo.wa_ok:   issues.append({"crit": True,  "t": "Sem WhatsApp no perfil",       "d": "Clientes não têm como entrar em contato direto pelo perfil Google."})
        if not ultimo.site_ok: issues.append({"crit": True,  "t": "Sem site próprio vinculado",    "d": "Reduz autoridade e visibilidade nas buscas locais."})
        if not ultimo.hrs_ok:  issues.append({"crit": True,  "t": "Horários não preenchidos",      "d": "O Google penaliza perfis incompletos no ranqueamento."})
        if not ultimo.desc_ok: issues.append({"crit": False, "t": "Sem descrição do negócio",      "d": "Descrições com palavras-chave melhoram o SEO local."})
        if ultimo.nota < 4.4:  issues.append({"crit": True,  "t": f"Avaliação {ultimo.nota} — abaixo do ideal", "d": "Concorrentes com 4.5+ aparecem primeiro nos resultados."})
        if ultimo.avaliacoes < 150: issues.append({"crit": False, "t": f"Poucas avaliações ({ultimo.avaliacoes})", "d": "Estratégia de captação pode mudar isso rapidamente."})

    return render_template("negocio.html",
        neg=neg, ultimo=ultimo, diags=diags,
        chart_labels=json.dumps(chart_labels),
        chart_scores=json.dumps(chart_scores),
        chart_notas=json.dumps(chart_notas),
        issues=issues,
    )

# ══════════════════════════════════════
#  INIT DB
# ══════════════════════════════════════
def init_db():
    db.create_all()
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

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)

@app.route('/template-csv')
@login_required
def template_csv():
    from flask import Response
    csv_content = "nome,categoria,cidade,whatsapp\nMeu Negocio,Categoria,Brasilia,61999999999\n"
    return Response(csv_content, mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=template_gbp.csv'})
