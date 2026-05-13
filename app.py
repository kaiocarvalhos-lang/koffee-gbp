import os, json, threading, time
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import requests

# ══════════════════════════════════════
#  APP & DB
# ══════════════════════════════════════
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "koffee-gbp-secret-2025-mude-isso")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///koffee_gbp.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

SKEY    = os.environ.get("SERP_API_KEY", "05746604c702ad7a4456cbbf34ae1e356f6ed6b146a5f85f0ac9cdfb8a71f15e")
LAT, LNG = -15.7801, -47.9292

# ══════════════════════════════════════
#  MODELS
# ══════════════════════════════════════
class User(db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    nome     = db.Column(db.String(100), nullable=False)
    email    = db.Column(db.String(120), unique=True, nullable=False)
    senha    = db.Column(db.String(200), nullable=False)
    criado   = db.Column(db.DateTime, default=datetime.utcnow)

class Cafeteria(db.Model):
    id       = db.Column(db.Integer, primary_key=True)
    nome     = db.Column(db.String(200), nullable=False)
    categoria= db.Column(db.String(100))
    bairro   = db.Column(db.String(100))
    wa       = db.Column(db.String(50))
    cid      = db.Column(db.String(30))
    r_base   = db.Column(db.Float)
    a_base   = db.Column(db.Integer)
    diagnosticos = db.relationship("Diagnostico", backref="cafeteria", lazy=True, order_by="Diagnostico.data.desc()")

class Diagnostico(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    cafe_id     = db.Column(db.Integer, db.ForeignKey("cafeteria.id"), nullable=False)
    data        = db.Column(db.DateTime, default=datetime.utcnow)
    score       = db.Column(db.Integer)
    nota        = db.Column(db.Float)
    avaliacoes  = db.Column(db.Integer)
    site_ok     = db.Column(db.Boolean, default=False)
    wa_ok       = db.Column(db.Boolean, default=False)
    hrs_ok      = db.Column(db.Boolean, default=False)
    desc_ok     = db.Column(db.Boolean, default=False)
    foto_ok     = db.Column(db.Boolean, default=False)
    preco       = db.Column(db.String(20))
    open_state  = db.Column(db.String(100))
    website     = db.Column(db.String(300))
    telefone    = db.Column(db.String(50))
    descricao   = db.Column(db.Text)
    titulo_gmb  = db.Column(db.String(200))
    raw_json    = db.Column(db.Text)

class JobStatus(db.Model):
    id          = db.Column(db.Integer, primary_key=True)
    rodando     = db.Column(db.Boolean, default=False)
    total       = db.Column(db.Integer, default=0)
    feitos      = db.Column(db.Integer, default=0)
    atual       = db.Column(db.String(200), default="")
    iniciado    = db.Column(db.DateTime)
    finalizado  = db.Column(db.DateTime)
    erros       = db.Column(db.Integer, default=0)

# ══════════════════════════════════════
#  DADOS DAS CAFETERIAS
# ══════════════════════════════════════
CAFES_SEED = [
    {"nome":"Los Baristas . Casa de Cafés",    "cat":"Café",         "bairro":"Asa Norte","wa":"61 3797-5043",  "r":4.5,"a":1171, "cid":"17583163971476992510"},
    {"nome":"L'amour du Pain",                 "cat":"Padaria",      "bairro":"Asa Sul",  "wa":"",             "r":4.4,"a":2584, "cid":"6702710620527701856"},
    {"nome":"Ernesto Cafés Especiais Asa Sul",  "cat":"Cafeteria",    "bairro":"Asa Sul",  "wa":"61 99453-1302","r":4.4,"a":6485, "cid":"12078012056153487854"},
    {"nome":"Belini Café",                      "cat":"Café",         "bairro":"Asa Sul",  "wa":"",             "r":4.3,"a":2062, "cid":"11492492020034622893"},
    {"nome":"Quanto Café",                      "cat":"Espresso bar", "bairro":"Asa Norte","wa":"",             "r":4.7,"a":731,  "cid":"13798753876965949483"},
    {"nome":"Mercado do Café de Brasília",      "cat":"Torrefação",   "bairro":"Asa Sul",  "wa":"",             "r":4.5,"a":1038, "cid":"4111121085812550731"},
    {"nome":"Café e um Chêro Asa Norte",        "cat":"Cafeteria",    "bairro":"Asa Norte","wa":"",             "r":4.6,"a":3346, "cid":"2671765463881500059"},
    {"nome":"Jacket Cafeteria Cafés Especiais", "cat":"Cafeteria",    "bairro":"Asa Sul",  "wa":"",             "r":4.4,"a":1058, "cid":"17142565482496007744"},
    {"nome":"Castália 102",                     "cat":"Café",         "bairro":"Asa Norte","wa":"",             "r":4.6,"a":2243, "cid":"13463474996864879954"},
    {"nome":"Martinica Café",                   "cat":"Espresso bar", "bairro":"Asa Norte","wa":"",             "r":4.4,"a":671,  "cid":"11261385242187475829"},
    {"nome":"Lale Café e Doceria",              "cat":"Cafeteria",    "bairro":"Asa Sul",  "wa":"5561993263835","r":4.5,"a":642,  "cid":"9913433593716353331"},
    {"nome":"Muy Café Brasília",                "cat":"Espresso bar", "bairro":"Asa Sul",  "wa":"",             "r":5.0,"a":65,   "cid":"6853351202326318375"},
    {"nome":"Café S/A Brasília",                "cat":"Cafeteria",    "bairro":"Asa Sul",  "wa":"",             "r":4.5,"a":311,  "cid":"8816461950136898959"},
    {"nome":"Daniel Briand Pâtissier",          "cat":"Cafeteria",    "bairro":"Asa Norte","wa":"556191041644", "r":4.4,"a":5599, "cid":"1839233293858105539"},
    {"nome":"Café Filó",                        "cat":"Cafeteria",    "bairro":"Sudoeste", "wa":"",             "r":4.5,"a":327,  "cid":"10673671769827984443"},
    {"nome":"Café e um Chêro Asa Sul",          "cat":"Cafeteria",    "bairro":"Asa Sul",  "wa":"556183516420", "r":4.6,"a":2504, "cid":"13263421227151490611"},
    {"nome":"Café Bela Vista",                  "cat":"Cafeteria",    "bairro":"Asa Norte","wa":"",             "r":4.9,"a":163,  "cid":"6768139518399239873"},
    {"nome":"Café Angelita Bistrô",             "cat":"Espresso bar", "bairro":"Asa Sul",  "wa":"5561996416980","r":4.5,"a":1084, "cid":"5068181316194465680"},
    {"nome":"Brasília Coffee",                  "cat":"Café",         "bairro":"Asa Norte","wa":"5561984425829","r":4.5,"a":143,  "cid":"17604268875014611929"},
    {"nome":"Vert Café",                        "cat":"Cafeteria",    "bairro":"Asa Sul",  "wa":"",             "r":4.4,"a":1812, "cid":"4593529204113179656"},
    {"nome":"Bambu Brasil Café Bistrô",         "cat":"Bistrô",       "bairro":"Asa Norte","wa":"",             "r":4.7,"a":118,  "cid":"13233455145615019840"},
    {"nome":"Constantina Café",                 "cat":"Cafeteria",    "bairro":"Asa Sul",  "wa":"",             "r":4.3,"a":952,  "cid":"1598875567674444824"},
    {"nome":"Civitá Café",                      "cat":"Cafeteria",    "bairro":"Asa Norte","wa":"",             "r":4.6,"a":371,  "cid":"17962789760271177158"},
    {"nome":"Lazo Café de Especialidade",       "cat":"Cafeteria",    "bairro":"Brasília", "wa":"",             "r":4.9,"a":72,   "cid":"12026544689001944953"},
    {"nome":"Oliva Cafe",                       "cat":"Café",         "bairro":"Noroeste", "wa":"",             "r":4.9,"a":278,  "cid":"11462448037171532113"},
    {"nome":"Cheirin Bão 313 Norte",            "cat":"Cafeteria",    "bairro":"Asa Norte","wa":"",             "r":4.9,"a":811,  "cid":"9884891372000582260"},
    {"nome":"Crioula Café",                     "cat":"Café",         "bairro":"Guará",    "wa":"",             "r":4.4,"a":485,  "cid":"3940515192284440879"},
    {"nome":"Alegro",                           "cat":"Cafeteria",    "bairro":"Sudoeste", "wa":"",             "r":4.4,"a":1127, "cid":"12535705145097584753"},
    {"nome":"BSB Coffee",                       "cat":"Cafeteria",    "bairro":"Sudoeste", "wa":"",             "r":4.4,"a":100,  "cid":"17141759581744392593"},
    {"nome":"Möca Café",                        "cat":"Confeitaria",  "bairro":"Asa Sul",  "wa":"61993993533",  "r":4.3,"a":612,  "cid":"14369939565934527090"},
    {"nome":"Amiste Café Brasília",             "cat":"Loja de café", "bairro":"Brasília", "wa":"5561998798030","r":5.0,"a":147,  "cid":"14531658473580728520"},
    {"nome":"Cafezinho Café Bistrô",            "cat":"Cafeteria",    "bairro":"Asa Norte","wa":"",             "r":4.6,"a":418,  "cid":"9677819925579756944"},
    {"nome":"Dylan Cafe Bakery",                "cat":"Café",         "bairro":"Asa Sul",  "wa":"",             "r":4.3,"a":602,  "cid":"18023485463620534351"},
]

# ══════════════════════════════════════
#  SERP API
# ══════════════════════════════════════
def _serp(params):
    params.update({"hl":"pt","gl":"br","api_key":SKEY})
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
            hrs[item["day"]] = item.get("hours","")
    return {
        "title":          place.get("title",""),
        "rating":         place.get("rating"),
        "reviews":        place.get("reviews"),
        "address":        place.get("address",""),
        "phone":          place.get("phone",""),
        "website":        place.get("website",""),
        "open_state":     place.get("open_state",""),
        "operating_hours":hrs if hrs else None,
        "description":    place.get("description",""),
        "price":          place.get("price",""),
        "thumbnail":      place.get("thumbnail",""),
    }

def buscar_cafe_api(cafe):
    # Camada 1: CID direto
    if cafe.cid:
        try:
            data = _serp({"engine":"google_maps","type":"place","data_cid":cafe.cid})
            place = data.get("place_results") or {}
            if place.get("title"):
                return _normalize(place), None
        except Exception:
            pass
    # Camada 2: busca por nome
    queries = [
        f"{cafe.nome} {cafe.bairro} Brasília",
        f"{cafe.nome} Brasília DF",
    ]
    for q in queries:
        try:
            data = _serp({"engine":"google_maps","type":"search","q":q,"ll":f"@{LAT},{LNG},13z"})
            results = data.get("local_results",[])
            if results:
                palavras = cafe.nome.lower().split()[:2]
                for res in results[:3]:
                    if any(p in (res.get("title","")).lower() for p in palavras if len(p)>3):
                        return res, None
        except Exception:
            continue
    return None, "Não encontrado"

def calc_score(r, wa):
    nota = float(r.get("rating") or 0)
    avs  = int(r.get("reviews") or 0)
    site = bool(r.get("website")) and "instagram.com" not in str(r.get("website","")) and "facebook.com" not in str(r.get("website",""))
    hrs  = bool(r.get("operating_hours") or r.get("open_state"))
    desc = bool(r.get("description"))
    foto = bool(r.get("thumbnail"))
    p = 0
    p += 35 if nota>=4.7 else 28 if nota>=4.5 else 20 if nota>=4.3 else 10
    p += 20 if avs>=2000 else 15 if avs>=500 else 9 if avs>=100 else 3
    if site: p+=15
    if wa:   p+=10
    if hrs:  p+=10
    if desc: p+=5
    if foto: p+=5
    return min(p,100), site, hrs, desc, foto

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

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        senha = request.form.get("senha","")
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
    cafes = Cafeteria.query.all()
    job   = JobStatus.query.first()

    cafe_data = []
    for c in cafes:
        ultimo = c.diagnosticos[0] if c.diagnosticos else None
        cafe_data.append({
            "cafe":  c,
            "ultimo": ultimo,
            "score_class": score_class(ultimo.score if ultimo else None),
        })

    # resumo
    com_diag   = [d for d in cafe_data if d["ultimo"]]
    criticas   = sum(1 for d in com_diag if d["ultimo"].score < 50)
    sem_site   = sum(1 for d in com_diag if not d["ultimo"].site_ok)
    sem_wa     = sum(1 for c in cafes if not c.wa)
    media_score= round(sum(d["ultimo"].score for d in com_diag)/len(com_diag)) if com_diag else 0

    return render_template("dashboard.html",
        cafe_data=cafe_data, job=job,
        criticas=criticas, sem_site=sem_site,
        sem_wa=sem_wa, media_score=media_score,
        total=len(cafes), analisadas=len(com_diag)
    )

# ══════════════════════════════════════
#  DIAGNÓSTICO
# ══════════════════════════════════════
_job_lock = threading.Lock()

@app.route("/analisar", methods=["POST"])
@login_required
def analisar_todas():
    job = JobStatus.query.first()
    if job and job.rodando:
        return jsonify({"ok":False,"msg":"Análise já em execução"})
    if not job:
        job = JobStatus()
        db.session.add(job)
    job.rodando    = True
    job.total      = Cafeteria.query.count()
    job.feitos     = 0
    job.erros      = 0
    job.iniciado   = datetime.utcnow()
    job.finalizado = None
    job.atual      = "Iniciando…"
    db.session.commit()
    threading.Thread(target=_run_all, daemon=True).start()
    return jsonify({"ok":True})

def _run_all():
    with app.app_context():
        cafes = Cafeteria.query.all()
        job   = JobStatus.query.first()
        for i, cafe in enumerate(cafes):
            with _job_lock:
                job.atual  = cafe.nome
                job.feitos = i
                db.session.commit()
            try:
                r, err = buscar_cafe_api(cafe)
                if r:
                    _salvar_diagnostico(cafe, r)
                else:
                    job.erros += 1
            except Exception:
                job.erros += 1
            time.sleep(0.8)
        job.rodando    = False
        job.feitos     = len(cafes)
        job.atual      = "Concluído"
        job.finalizado = datetime.utcnow()
        db.session.commit()

@app.route("/analisar/<int:cafe_id>", methods=["POST"])
@login_required
def analisar_um(cafe_id):
    cafe = Cafeteria.query.get_or_404(cafe_id)
    try:
        r, err = buscar_cafe_api(cafe)
        if r:
            _salvar_diagnostico(cafe, r)
            return jsonify({"ok":True})
        return jsonify({"ok":False,"msg":err})
    except Exception as e:
        return jsonify({"ok":False,"msg":str(e)})

def _salvar_diagnostico(cafe, r):
    score, site_ok, hrs_ok, desc_ok, foto_ok = calc_score(r, cafe.wa)
    d = Diagnostico(
        cafe_id    = cafe.id,
        score      = score,
        nota       = float(r.get("rating") or cafe.r_base),
        avaliacoes = int(r.get("reviews") or cafe.a_base),
        site_ok    = site_ok,
        wa_ok      = bool(cafe.wa),
        hrs_ok     = hrs_ok,
        desc_ok    = desc_ok,
        foto_ok    = foto_ok,
        preco      = r.get("price",""),
        open_state = r.get("open_state",""),
        website    = r.get("website",""),
        telefone   = r.get("phone",""),
        descricao  = r.get("description",""),
        titulo_gmb = r.get("title",""),
        raw_json   = json.dumps(r, ensure_ascii=False),
    )
    db.session.add(d)
    db.session.commit()

@app.route("/job-status")
@login_required
def job_status():
    job = JobStatus.query.first()
    if not job:
        return jsonify({"rodando":False,"feitos":0,"total":0,"atual":"","erros":0})
    pct = int(job.feitos/job.total*100) if job.total else 0
    return jsonify({
        "rodando":   job.rodando,
        "feitos":    job.feitos,
        "total":     job.total,
        "atual":     job.atual,
        "erros":     job.erros,
        "pct":       pct,
        "finalizado": job.finalizado.strftime("%d/%m %H:%M") if job.finalizado else None,
    })

# ══════════════════════════════════════
#  DETALHE DA CAFETERIA
# ══════════════════════════════════════
@app.route("/cafeteria/<int:cafe_id>")
@login_required
def cafeteria(cafe_id):
    cafe  = Cafeteria.query.get_or_404(cafe_id)
    diags = cafe.diagnosticos  # ordenados por data desc
    ultimo = diags[0] if diags else None

    # dados para gráfico de evolução (últimos 10)
    historico = list(reversed(diags[:10]))
    chart_labels = [d.data.strftime("%d/%m") for d in historico]
    chart_scores = [d.score for d in historico]
    chart_notas  = [d.nota  for d in historico]

    # issues
    issues = []
    if ultimo:
        if not ultimo.wa_ok:   issues.append({"crit":True, "t":"Sem WhatsApp no perfil", "d":"Nenhum WhatsApp encontrado. Clientes não conseguem contato direto."})
        if not ultimo.site_ok: issues.append({"crit":True, "t":"Sem site próprio vinculado", "d":"Reduz autoridade e visibilidade nas buscas locais."})
        if not ultimo.hrs_ok:  issues.append({"crit":True, "t":"Horários não preenchidos", "d":"O Google penaliza perfis incompletos."})
        if not ultimo.desc_ok: issues.append({"crit":False,"t":"Sem descrição do negócio", "d":"Descrições com palavras-chave melhoram o SEO local."})
        if ultimo.nota < 4.4:  issues.append({"crit":True, "t":f"Avaliação {ultimo.nota} — abaixo do ideal", "d":"Concorrentes com 4.5+ aparecem primeiro."})
        if ultimo.avaliacoes < 150: issues.append({"crit":False,"t":f"Poucas avaliações ({ultimo.avaliacoes})", "d":"Uma estratégia de captação pode mudar isso rápido."})

    return render_template("cafeteria.html",
        cafe=cafe, ultimo=ultimo, diags=diags,
        chart_labels=json.dumps(chart_labels),
        chart_scores=json.dumps(chart_scores),
        chart_notas=json.dumps(chart_notas),
        issues=issues,
        score_class=score_class,
    )

# ══════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════
def score_class(score):
    if score is None: return "idle"
    if score >= 75: return "ok"
    if score >= 50: return "warn"
    return "bad"

app.jinja_env.globals["score_class"] = score_class

# ══════════════════════════════════════
#  INIT DB
# ══════════════════════════════════════
def init_db():
    db.create_all()
    # criar usuário admin padrão
    if not User.query.first():
        admin = User(
            nome  = "Kaio Carvalho",
            email = "kaio@koffeemarketing.com.br",
            senha = generate_password_hash("koffee2025"),
        )
        db.session.add(admin)
        db.session.commit()
        print("✓ Usuário admin criado: kaio@koffeemarketing.com.br / koffee2025")
    # popular cafeterias
    if not Cafeteria.query.first():
        for c in CAFES_SEED:
            db.session.add(Cafeteria(
                nome=c["nome"], categoria=c["cat"], bairro=c["bairro"],
                wa=c["wa"], cid=c["cid"], r_base=c["r"], a_base=c["a"]
            ))
        db.session.commit()
        print(f"✓ {len(CAFES_SEED)} cafeterias importadas")
    # job status inicial
    if not JobStatus.query.first():
        db.session.add(JobStatus())
        db.session.commit()

if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=False, host="0.0.0.0", port=5000)
