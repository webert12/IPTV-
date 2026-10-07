import os
import json
import uuid
import base64
import hashlib
import secrets
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

import requests
from flask import (
    Flask,
    request,
    redirect,
    url_for,
    session,
    render_template_string,
    jsonify,
    flash,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


# ============================================================
# CONFIGURAÇÃO
# ============================================================

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "").strip().rstrip("/")
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DB_URL", "")
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
ADMIN_USER = os.getenv("ADMIN_USER", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()

PAGBANK_TOKEN = os.getenv("PAGBANK_TOKEN", "").strip()
PAGBANK_ENV = os.getenv("PAGBANK_ENV", "production").strip().lower()
PAGBANK_TIMEOUT = int(os.getenv("PAGBANK_TIMEOUT", "25"))

PAGBANK_BASE_URL = (
    "https://api.pagseguro.com"
    if PAGBANK_ENV == "production"
    else "https://sandbox.api.pagseguro.com"
)

if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(48)

app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=APP_PUBLIC_URL.startswith("https://"),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL.startswith("postgresql://") and "+psycopg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL/DB_URL não configurada.")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=3,
    max_overflow=2,
)

Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# ============================================================
# MODELOS
# Usa a tabela clientes que já existe no seu sistema.
# ============================================================

class Cliente(Base):
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(150), nullable=False)
    usuario = Column(String(150), nullable=False, unique=True, index=True)
    valor = Column(Float, nullable=False, default=0.0)
    vencimento = Column(Date, nullable=True)
    status = Column(String(20), nullable=False, default="Pendente")
    data_pagamento = Column(DateTime, nullable=True)
    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)


class Pagamento(Base):
    __tablename__ = "pagamentos"

    id = Column(Integer, primary_key=True)
    referencia = Column(String(80), nullable=False, unique=True, index=True)
    cliente_id = Column(Integer, nullable=True, index=True)
    nome_cliente = Column(String(150), nullable=True)
    usuario_cliente = Column(String(150), nullable=True)

    valor = Column(Float, nullable=False)
    valor_centavos = Column(Integer, nullable=False)

    status = Column(String(40), nullable=False, default="AGUARDANDO", index=True)
    metodo = Column(String(20), nullable=False, default="PIX")

    pagbank_order_id = Column(String(100), nullable=True, unique=True, index=True)
    pagbank_charge_id = Column(String(100), nullable=True, index=True)

    qr_code_text = Column(Text, nullable=True)
    qr_code_url = Column(Text, nullable=True)

    criado_em = Column(DateTime, nullable=False, default=datetime.utcnow)
    pago_em = Column(DateTime, nullable=True)
    vencimento_gerado = Column(Date, nullable=True)
    webhook_recebido_em = Column(DateTime, nullable=True)

    observacao = Column(Text, nullable=True)
    idempotency_key = Column(String(100), nullable=True, unique=True)


class WebhookEvento(Base):
    __tablename__ = "pagbank_webhook_eventos"

    id = Column(Integer, primary_key=True)
    evento_id = Column(String(180), nullable=True, unique=True, index=True)
    order_id = Column(String(100), nullable=True, index=True)
    charge_id = Column(String(100), nullable=True, index=True)
    payload_hash = Column(String(64), nullable=False, unique=True, index=True)
    recebido_em = Column(DateTime, nullable=False, default=datetime.utcnow)
    processado_em = Column(DateTime, nullable=True)
    status = Column(String(30), nullable=False, default="RECEBIDO")
    detalhe = Column(Text, nullable=True)


class SistemaConfig(Base):
    __tablename__ = "pagbank_config"

    id = Column(Integer, primary_key=True)
    chave = Column(String(100), nullable=False, unique=True)
    valor = Column(Text, nullable=False)
    atualizado_em = Column(DateTime, nullable=False, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


# ============================================================
# HELPERS
# ============================================================

def agora_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dinheiro(valor):
    return (
        f"R$ {float(valor):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def parse_centavos(valor_bruto):
    texto = str(valor_bruto or "").strip()
    texto = texto.replace("R$", "").replace(" ", "")

    if not texto:
        raise ValueError("Informe o valor.")

    # Aceita:
    # 25
    # 25,00
    # 25.00
    # 1.234,56
    # 1,234.56
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        # 25.00 continua como decimal.
        pass

    try:
        valor = Decimal(texto).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError("Valor inválido.")

    centavos = int(valor * 100)

    if centavos < 100:
        raise ValueError("O valor mínimo é R$ 1,00.")

    if centavos > 5000000:
        raise ValueError("O valor máximo permitido é R$ 50.000,00.")

    return centavos


def proximo_vencimento():
    hoje = date.today()
    if hoje.month == 12:
        return date(hoje.year + 1, 1, 10)
    return date(hoje.year, hoje.month + 1, 10)


def csrf_token():
    if "csrf_pagamento" not in session:
        session["csrf_pagamento"] = secrets.token_urlsafe(32)
    return session["csrf_pagamento"]


def validar_csrf(token):
    esperado = session.get("csrf_pagamento")
    return bool(esperado and token and secrets.compare_digest(esperado, token))


def public_url(path=""):
    if not APP_PUBLIC_URL:
        raise RuntimeError("APP_PUBLIC_URL não configurada.")
    return APP_PUBLIC_URL + "/" + path.lstrip("/")


def pagbank_headers(idempotency_key=None):
    headers = {
        "Authorization": f"Bearer {PAGBANK_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["x-idempotency-key"] = idempotency_key
    return headers


def json_response_error(response):
    try:
        data = response.json()
    except Exception:
        data = {"message": response.text[:500]}
    return data


def obter_config(chave):
    db = SessionLocal()
    try:
        item = db.query(SistemaConfig).filter(SistemaConfig.chave == chave).first()
        return item.valor if item else None
    finally:
        db.close()


def salvar_config(chave, valor):
    db = SessionLocal()
    try:
        item = db.query(SistemaConfig).filter(SistemaConfig.chave == chave).first()
        if item:
            item.valor = valor
            item.atualizado_em = agora_utc()
        else:
            db.add(SistemaConfig(
                chave=chave,
                valor=valor,
                atualizado_em=agora_utc(),
            ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ============================================================
# PAGBANK
# ============================================================

def criar_pedido_pix(pagamento):
    if not PAGBANK_TOKEN:
        raise RuntimeError("PAGBANK_TOKEN não configurado.")

    if not APP_PUBLIC_URL.startswith("https://"):
        raise RuntimeError(
            "APP_PUBLIC_URL precisa usar HTTPS para receber o webhook."
        )

    expiracao = datetime.now(timezone.utc) + timedelta(minutes=30)

    payload = {
        "reference_id": pagamento.referencia,
        "items": [
            {
                "reference_id": pagamento.referencia,
                "name": "Renovação IPTV",
                "quantity": 1,
                "unit_amount": pagamento.valor_centavos,
            }
        ],
        "notification_urls": [
            public_url("/webhook/pagbank")
        ],
        "charges": [
            {
                "reference_id": pagamento.referencia,
                "description": "Renovação IPTV",
                "amount": {
                    "value": pagamento.valor_centavos,
                    "currency": "BRL",
                },
                "payment_method": {
                    "type": "PIX",
                    "pix": {
                        "expiration_date": expiracao.isoformat().replace("+00:00", "Z")
                    },
                },
            }
        ],
    }

    resposta = requests.post(
        f"{PAGBANK_BASE_URL}/orders",
        headers=pagbank_headers(pagamento.idempotency_key),
        json=payload,
        timeout=PAGBANK_TIMEOUT,
    )

    if resposta.status_code not in (200, 201):
        erro = json_response_error(resposta)
        raise RuntimeError(
            f"PagBank recusou a criação do PIX ({resposta.status_code}): "
            f"{json.dumps(erro, ensure_ascii=False)[:800]}"
        )

    dados = resposta.json()
    charges = dados.get("charges") or []

    if not charges:
        raise RuntimeError("PagBank não retornou a cobrança PIX.")

    charge = charges[0]
    qr_code = charge.get("qr_code") or {}

    pagamento.pagbank_order_id = dados.get("id")
    pagamento.pagbank_charge_id = charge.get("id")
    pagamento.qr_code_text = qr_code.get("text")
    pagamento.status = charge.get("status") or "WAITING"

    links = charge.get("links") or []
    for link in links:
        if link.get("rel") == "QRCODE.PNG":
            pagamento.qr_code_url = link.get("href")
            break

    if not pagamento.qr_code_text:
        raise RuntimeError("PagBank não retornou o Pix copia e cola.")

    db = SessionLocal()
    try:
        registro = db.query(Pagamento).filter(Pagamento.id == pagamento.id).first()
        if not registro:
            raise RuntimeError("Pagamento não encontrado.")

        registro.pagbank_order_id = pagamento.pagbank_order_id
        registro.pagbank_charge_id = pagamento.pagbank_charge_id
        registro.qr_code_text = pagamento.qr_code_text
        registro.qr_code_url = pagamento.qr_code_url
        registro.status = pagamento.status
        db.commit()
    finally:
        db.close()

    return dados


def consultar_pedido(order_id):
    resposta = requests.get(
        f"{PAGBANK_BASE_URL}/orders/{order_id}",
        headers=pagbank_headers(),
        timeout=PAGBANK_TIMEOUT,
    )

    if resposta.status_code != 200:
        return None

    return resposta.json()


def obter_public_key_webhook(forcar=False):
    """Obtém e mantém em cache a chave pública usada pelo PagBank para webhooks.
    Se forcar=True, busca uma chave nova no PagBank (útil em caso de rotação da chave).
    """
    chave_env = os.getenv("PAGBANK_WEBHOOK_PUBLIC_KEY", "").strip()
    if chave_env:
        return chave_env

    if not forcar:
        chave_cache = obter_config("PAGBANK_WEBHOOK_PUBLIC_KEY")
        if chave_cache:
            return chave_cache

    resposta = requests.get(
        f"{PAGBANK_BASE_URL}/public-keys",
        params={"type": "webhook"},
        headers=pagbank_headers(),
        timeout=PAGBANK_TIMEOUT,
    )

    if resposta.status_code != 200:
        raise RuntimeError(
            f"Não foi possível obter a chave pública do webhook: "
            f"{resposta.status_code}"
        )

    dados = resposta.json()
    public_key = dados.get("public_key")

    if not public_key:
        raise RuntimeError("PagBank não retornou public_key.")

    salvar_config("PAGBANK_WEBHOOK_PUBLIC_KEY", public_key)
    return public_key


def verificar_assinatura_webhook(raw_body, header, forcar=False):
    if not header:
        return False

    public_key_b64 = obter_public_key_webhook(forcar=forcar)

    try:
        public_key_der = base64.b64decode(public_key_b64)
        public_key = serialization.load_der_public_key(public_key_der)
    except Exception:
        return False

    assinaturas = []

    for parte in header.split(","):
        parte = parte.strip()
        if parte:
            assinaturas.append(parte)

    for assinatura in assinaturas:
        try:
            assinatura_bytes = base64.b64decode(assinatura)
            public_key.verify(
                assinatura_bytes,
                raw_body,
                ec.ECDSA(hashes.SHA256()),
            )
            return True
        except Exception:
            continue

    return False

def extrair_dados_pagbank(payload):
    charges = payload.get("charges") or []
    charge = charges[0] if charges else {}

    referencia = (
        payload.get("reference_id")
        or charge.get("reference_id")
    )

    return {
        "order_id": payload.get("id"),
        "charge_id": charge.get("id"),
        "reference_id": referencia,
        "status": charge.get("status") or payload.get("status"),
        "valor": (
            (charge.get("amount") or {}).get("value")
            or (charge.get("amount") or {}).get("summary", {}).get("paid")
        ),
        "paid_at": charge.get("paid_at"),
    }


def processar_pagamento_pago(db, pagamento, valor_confirmado):
    """Confirma o PIX e renova o cliente automaticamente.

    A função é idempotente: se o mesmo pagamento for processado novamente
    pelo webhook ou pela consulta automática da página, o vencimento não é
    alterado duas vezes.
    """
    if pagamento.status == "PAGO" and pagamento.vencimento_gerado:
        return True

    if pagamento.status == "RECEBIDO_SEM_VINCULO" and pagamento.pago_em:
        # Só impede processamento duplicado quando já houve confirmação real.
        # Um pagamento criado pelo webhook sem vínculo ainda pode ser ligado
        # posteriormente caso o cadastro seja localizado.
        if pagamento.cliente_id:
            cliente_existente = (
                db.query(Cliente)
                .filter(Cliente.id == pagamento.cliente_id)
                .first()
            )
            if cliente_existente:
                pagamento.status = "AGUARDANDO"
            else:
                return False

    if valor_confirmado is not None:
        try:
            valor_confirmado = int(valor_confirmado)
        except (TypeError, ValueError):
            valor_confirmado = None

    if valor_confirmado is not None and valor_confirmado != pagamento.valor_centavos:
        pagamento.status = "ERRO_VALOR"
        pagamento.observacao = (
            f"Valor PagBank divergente. Esperado {pagamento.valor_centavos}, "
            f"recebido {valor_confirmado}."
        )
        return False

    cliente = None

    if pagamento.cliente_id:
        cliente = (
            db.query(Cliente)
            .filter(Cliente.id == pagamento.cliente_id)
            .first()
        )

    # Fallback por usuário caso o vínculo por ID não exista mais.
    if not cliente and pagamento.usuario_cliente:
        cliente = (
            db.query(Cliente)
            .filter(Cliente.usuario == pagamento.usuario_cliente)
            .first()
        )

    if cliente:
        agora = agora_utc()
        novo_vencimento = proximo_vencimento()

        cliente.status = "Pago"
        cliente.valor = float(pagamento.valor)
        cliente.data_pagamento = agora
        cliente.vencimento = novo_vencimento

        pagamento.cliente_id = cliente.id
        pagamento.nome_cliente = cliente.nome
        pagamento.usuario_cliente = cliente.usuario
        pagamento.status = "PAGO"
        pagamento.pago_em = pagamento.pago_em or agora
        pagamento.vencimento_gerado = novo_vencimento
        pagamento.observacao = "Pagamento confirmado automaticamente pelo PagBank e cliente renovado."
        return True

    pagamento.status = "RECEBIDO_SEM_VINCULO"
    pagamento.pago_em = pagamento.pago_em or agora_utc()
    pagamento.observacao = (
        "Pagamento recebido pelo PagBank, mas o cliente não foi encontrado "
        "para atualização automática."
    )
    return False


# ============================================================
# AUTENTICAÇÃO ADMINISTRATIVA
# ============================================================

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logado"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


# ============================================================
# HTML
# ============================================================

BASE_CSS = """
<style>
* { box-sizing: border-box; }
body {
    background: #07111f;
    color: #f8fafc;
    font-family: Arial, sans-serif;
    margin: 0;
}
.page {
    min-height: 100vh;
    padding: 28px 16px 50px;
}
.box {
    width: min(560px, 100%);
    margin: 0 auto;
}
.brand {
    text-align: center;
    margin-bottom: 22px;
}
.brand .icon {
    font-size: 48px;
}
.brand h1 {
    margin: 8px 0 4px;
    font-size: 30px;
}
.brand p {
    color: #94a3b8;
    margin: 0;
}
.card {
    background: #0d1b2d;
    border: 1px solid #1e334d;
    border-radius: 20px;
    padding: 22px;
    box-shadow: 0 18px 50px rgba(0,0,0,.25);
    margin-bottom: 16px;
}
label {
    display:block;
    margin: 0 0 7px;
    color:#cbd5e1;
    font-weight:700;
}
input, select {
    width:100%;
    padding:14px;
    border-radius:12px;
    border:1px solid #29425f;
    background:#081522;
    color:#fff;
    outline:none;
    margin-bottom:14px;
}
button, .btn {
    width:100%;
    border:0;
    border-radius:12px;
    padding:14px;
    background:#16a34a;
    color:#fff;
    font-size:16px;
    font-weight:800;
    cursor:pointer;
    text-decoration:none;
    display:inline-block;
    text-align:center;
}
.btn-secondary {
    background:#172a40;
}
.btn-danger {
    background:#b91c1c;
}
.client {
    display:block;
    padding:14px;
    margin:8px 0;
    border:1px solid #29425f;
    border-radius:13px;
    background:#091827;
    color:#fff;
    text-decoration:none;
}
.client:hover {
    border-color:#22c55e;
}
.client strong { display:block; font-size:16px; }
.client span { color:#94a3b8; font-size:13px; }
.pix {
    text-align:center;
}
.pix img {
    width:260px;
    max-width:100%;
    background:#fff;
    padding:10px;
    border-radius:16px;
}
.copybox {
    word-break:break-all;
    background:#07111f;
    border:1px solid #29425f;
    border-radius:12px;
    padding:13px;
    color:#cbd5e1;
    font-size:13px;
    margin:12px 0;
}
.status {
    text-align:center;
    padding:12px;
    border-radius:12px;
    background:#172a40;
    margin-top:14px;
}
.ok { color:#4ade80; }
.warn { color:#facc15; }
.err { color:#f87171; }
.small {
    color:#94a3b8;
    font-size:13px;
    line-height:1.5;
}
table {
    width:100%;
    border-collapse:collapse;
}
th, td {
    padding:10px;
    border-bottom:1px solid #20364e;
    text-align:left;
}
@media(max-width:600px) {
    .page { padding-top:18px; }
    .card { padding:17px; }
    .brand h1 { font-size:25px; }
}
</style>
"""


# ============================================================
# PÁGINA PÚBLICA
# ============================================================

@app.route("/", methods=["GET"])
@app.route("/pagar", methods=["GET"])
def pagar():
    busca = request.args.get("busca", "").strip()
    clientes = []

    if len(busca) >= 2:
        db = SessionLocal()
        try:
            termo = f"%{busca}%"
            clientes = (
                db.query(Cliente)
                .filter(Cliente.nome.ilike(termo))
                .order_by(Cliente.nome.asc())
                .limit(20)
                .all()
            )
        finally:
            db.close()

    return render_template_string(
        BASE_CSS + """
        <div class="page">
          <div class="box">
            <div class="brand">
              <div class="icon">📺</div>
              <h1>Renovação IPTV</h1>
              <p>Faça sua renovação de forma rápida e segura pelo PIX.</p>
            </div>

            <div class="card">
              <form method="get" action="{{ url_for('pagar') }}">
                <label>1. Pesquise seu nome</label>
                <input
                    name="busca"
                    value="{{ busca }}"
                    placeholder="Digite seu primeiro nome"
                    autocomplete="off"
                >
                <button type="submit">🔎 Pesquisar cadastro</button>
              </form>
            </div>

            {% if busca and not clientes %}
            <div class="card">
              <div class="warn">Nenhum cadastro encontrado.</div>
              <p class="small">
                Confira a forma como seu nome foi cadastrado e tente novamente.
              </p>
            </div>
            {% endif %}

            {% if clientes %}
            <div class="card">
              <h3>2. Selecione seu cadastro</h3>
              <p class="small">Escolha somente o seu próprio cadastro.</p>

              {% for cliente in clientes %}
              <a class="client"
                 href="{{ url_for('selecionar_cliente', cliente_id=cliente.id) }}">
                <strong>{{ cliente.nome }}</strong>
                <span>Usuário: {{ cliente.usuario }}</span>
              </a>
              {% endfor %}
            </div>
            {% endif %}

            <div class="card">
              <div class="small">
                🔒 O pagamento é processado pelo PagBank.<br>
                🔒 Seus dados administrativos não ficam disponíveis nesta página.
              </div>
            </div>
          </div>
        </div>
        """,
        busca=busca,
        clientes=clientes,
    )


@app.route("/selecionar/<int:cliente_id>")
def selecionar_cliente(cliente_id):
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        if not cliente:
            flash("Cliente não encontrado.")
            return redirect(url_for("pagar"))

        session["cliente_pagamento_id"] = cliente.id
        session["cliente_pagamento_nome"] = cliente.nome
        session["cliente_pagamento_usuario"] = cliente.usuario

        valor_padrao = float(cliente.valor or 0)
    finally:
        db.close()

    return render_template_string(
        BASE_CSS + """
        <div class="page">
          <div class="box">
            <div class="brand">
              <div class="icon">💳</div>
              <h1>Renovação IPTV</h1>
              <p>Confirme os dados para gerar seu PIX.</p>
            </div>

            <div class="card">
              <h3>Cadastro selecionado</h3>
              <p><strong>{{ nome }}</strong></p>
              <p class="small">Usuário: {{ usuario }}</p>
            </div>

            <div class="card">
              <form method="post" action="{{ url_for('confirmar_pagamento') }}">
                <input type="hidden" name="csrf" value="{{ csrf }}">

                <label>3. Valor da renovação</label>
                <input
                    type="text"
                    name="valor"
                    value="{{ valor }}"
                    inputmode="decimal"
                    placeholder="Ex.: 25,00"
                    required
                >

                <button type="submit">
                  Continuar para confirmar PIX
                </button>
              </form>

              <div style="height:10px"></div>
              <a class="btn btn-secondary" href="{{ url_for('pagar') }}">
                Voltar
              </a>
            </div>
          </div>
        </div>
        """,
        nome=session.get("cliente_pagamento_nome", ""),
        usuario=session.get("cliente_pagamento_usuario", ""),
        valor=f"{valor_padrao:.2f}".replace(".", ","),
        csrf=csrf_token(),
    )


@app.route("/confirmar-pagamento", methods=["POST"])
def confirmar_pagamento():
    if not validar_csrf(request.form.get("csrf")):
        return "Solicitação inválida.", 400

    cliente_id = session.get("cliente_pagamento_id")
    if not cliente_id:
        return redirect(url_for("pagar"))

    try:
        centavos = parse_centavos(request.form.get("valor"))
    except ValueError as exc:
        return render_template_string(
            BASE_CSS + """
            <div class="page"><div class="box">
              <div class="brand">
                <div class="icon">⚠️</div>
                <h1>Valor inválido</h1>
              </div>
              <div class="card">
                <p class="err">{{ erro }}</p>
                <a class="btn btn-secondary" href="{{ url_for('selecionar_cliente', cliente_id=cliente_id) }}">
                  Voltar
                </a>
              </div>
            </div></div>
            """,
            erro=str(exc),
            cliente_id=cliente_id,
        ), 400

    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()

        if not cliente:
            return redirect(url_for("pagar"))

        referencia = "IPTV-" + secrets.token_urlsafe(18)
        idempotency = str(uuid.uuid4())

        pagamento = Pagamento(
            referencia=referencia,
            cliente_id=cliente.id,
            nome_cliente=cliente.nome,
            usuario_cliente=cliente.usuario,
            valor=centavos / 100,
            valor_centavos=centavos,
            status="CRIANDO",
            metodo="PIX",
            idempotency_key=idempotency,
            criado_em=agora_utc(),
        )

        db.add(pagamento)
        db.commit()
        pagamento_id = pagamento.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        pagamento = db.query(Pagamento).filter(Pagamento.id == pagamento_id).first()

        try:
            criar_pedido_pix(pagamento)
        except Exception as exc:
            pagamento.status = "ERRO_CRIACAO"
            pagamento.observacao = str(exc)[:1000]
            db.commit()

            return render_template_string(
                BASE_CSS + """
                <div class="page"><div class="box">
                  <div class="brand">
                    <div class="icon">⚠️</div>
                    <h1>Não foi possível gerar o PIX</h1>
                  </div>
                  <div class="card">
                    <p class="err">
                      A cobrança não foi criada. Tente novamente em alguns instantes.
                    </p>
                    <a class="btn btn-secondary" href="{{ url_for('pagar') }}">
                      Voltar
                    </a>
                  </div>
                </div></div>
                """,
            ), 502

        return redirect(url_for("pix", referencia=pagamento.referencia))
    finally:
        db.close()


@app.route("/pix/<referencia>")
def pix(referencia):
    db = SessionLocal()
    try:
        pagamento = (
            db.query(Pagamento)
            .filter(Pagamento.referencia == referencia)
            .first()
        )

        if not pagamento:
            return redirect(url_for("pagar"))

        return render_template_string(
            BASE_CSS + """
            <div class="page">
              <div class="box">
                <div class="brand">
                  <div class="icon">📺</div>
                  <h1>Renovação IPTV</h1>
                  <p>Seu PIX foi gerado.</p>
                </div>

                <div class="card pix">
                  <h2>{{ valor }}</h2>
                  <p><strong>{{ nome }}</strong></p>

                  {% if pagamento.qr_code_url %}
                    <img src="{{ url_for('qrcode_proxy', referencia=pagamento.referencia) }}"
                         alt="QR Code PIX">
                  {% endif %}

                  <p class="small">
                    Escaneie o QR Code no aplicativo do seu banco.
                  </p>

                  <h4>PIX Copia e Cola</h4>
                  <div class="copybox" id="pixcode">{{ pagamento.qr_code_text }}</div>

                  <button type="button" onclick="copiarPix()">
                    📋 Copiar PIX
                  </button>

                  <div id="status" class="status">
                    ⏳ Aguardando confirmação do pagamento...
                  </div>

                  <p class="small">
                    Após o pagamento, a confirmação é feita automaticamente pelo PagBank.
                  </p>
                </div>
              </div>
            </div>

            <script>
            function copiarPix() {
              const texto = document.getElementById("pixcode").innerText;
              navigator.clipboard.writeText(texto).then(() => {
                alert("PIX copiado!");
              });
            }

            async function consultar() {
              try {
                const r = await fetch(
                  "{{ url_for('status_pagamento', referencia=pagamento.referencia) }}",
                  {cache: "no-store"}
                );
                const data = await r.json();

                const box = document.getElementById("status");

                if (data.status === "PAGO") {
                  box.innerHTML =
                    '<span class="ok">✅ Pagamento confirmado!</span><br>' +
                    'Seu vencimento foi atualizado para ' +
                    data.vencimento + '.';
                  return;
                }

                if (data.status === "RECEBIDO_SEM_VINCULO") {
                  box.innerHTML =
                    '<span class="warn">⚠️ Pagamento recebido.</span><br>' +
                    'A confirmação foi recebida e será verificada.';
                  return;
                }

                if (data.status === "EXPIRADO") {
                  box.innerHTML =
                    '<span class="err">PIX expirado.</span><br>' +
                    'Gere uma nova cobrança.';
                  return;
                }

                setTimeout(consultar, 5000);
              } catch (e) {
                setTimeout(consultar, 7000);
              }
            }

            consultar();
            </script>
            """,
            pagamento=pagamento,
            nome=pagamento.nome_cliente,
            valor=dinheiro(pagamento.valor),
        )
    finally:
        db.close()


@app.route("/qrcode/<referencia>.png")
def qrcode_proxy(referencia):
    db = SessionLocal()
    try:
        pagamento = (
            db.query(Pagamento)
            .filter(Pagamento.referencia == referencia)
            .first()
        )
        if not pagamento or not pagamento.qr_code_url:
            return "QR Code não encontrado.", 404

        resposta = requests.get(
            pagamento.qr_code_url,
            headers={"Authorization": f"Bearer {PAGBANK_TOKEN}"},
            timeout=PAGBANK_TIMEOUT,
        )

        if resposta.status_code != 200:
            return "QR Code indisponível.", 502

        return resposta.content, 200, {
            "Content-Type": resposta.headers.get("Content-Type", "image/png"),
            "Cache-Control": "no-store",
        }
    finally:
        db.close()


@app.route("/status/<referencia>")
def status_pagamento(referencia):
    db = SessionLocal()
    try:
        pagamento = (
            db.query(Pagamento)
            .filter(Pagamento.referencia == referencia)
            .first()
        )

        if not pagamento:
            return jsonify({"status": "NAO_ENCONTRADO"}), 404

        # O webhook é o mecanismo principal. Esta consulta ao PagBank é um
        # mecanismo de segurança: se o webhook estiver atrasado, bloqueado ou
        # ainda não tiver chegado, a própria página consegue confirmar o PIX.
        if (
            pagamento.status not in ("PAGO", "RECEBIDO_SEM_VINCULO", "CANCELADO", "EXPIRADO", "ERRO_VALOR")
            and pagamento.pagbank_order_id
        ):
            try:
                pedido = consultar_pedido(pagamento.pagbank_order_id)
                if pedido:
                    dados = extrair_dados_pagbank(pedido)
                    status_oficial = str(dados.get("status") or "").upper()

                    if status_oficial == "PAID":
                        processar_pagamento_pago(
                            db,
                            pagamento,
                            dados.get("valor"),
                        )
                        db.commit()
                    elif status_oficial in ("CANCELED", "CANCELLED", "DECLINED"):
                        pagamento.status = "CANCELADO"
                        db.commit()
                    elif status_oficial == "WAITING":
                        pagamento.status = "AGUARDANDO"
                        db.commit()
            except Exception as exc:
                # O status da tela continua sendo o último estado conhecido.
                # O webhook permanece responsável pela confirmação oficial.
                print(f"⚠️ Consulta automática PagBank falhou: {exc}")
                db.rollback()

        vencimento = (
            pagamento.vencimento_gerado.strftime("%d/%m/%Y")
            if pagamento.vencimento_gerado
            else ""
        )

        return jsonify({
            "status": pagamento.status,
            "vencimento": vencimento,
            "renovado": pagamento.status == "PAGO" and bool(pagamento.vencimento_gerado),
        })
    finally:
        db.close()


# ============================================================
# WEBHOOK PAGBANK
# ============================================================

@app.route("/webhook/pagbank", methods=["POST"])
def webhook_pagbank():
    raw_body = request.get_data(cache=True, as_text=False)
    assinatura = request.headers.get("x-payload-signature", "")

    try:
        assinatura_valida = verificar_assinatura_webhook(raw_body, assinatura)

        # Se a chave armazenada tiver sido rotacionada pelo PagBank, busca a
        # chave pública atual uma segunda vez antes de rejeitar a notificação.
        if not assinatura_valida:
            assinatura_valida = verificar_assinatura_webhook(
                raw_body,
                assinatura,
                forcar=True,
            )

        if not assinatura_valida:
            return "Assinatura inválida.", 401
    except Exception as exc:
        print(f"⚠️ Falha na validação do webhook PagBank: {exc}")
        return "Falha na validação.", 401

    payload_hash = hashlib.sha256(raw_body).hexdigest()

    db = SessionLocal()
    evento = None

    try:
        existente = (
            db.query(WebhookEvento)
            .filter(WebhookEvento.payload_hash == payload_hash)
            .first()
        )

        if existente:
            return "", 204

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            return "JSON inválido.", 400

        dados = extrair_dados_pagbank(payload)

        evento = WebhookEvento(
            evento_id=str(payload.get("id") or payload_hash),
            order_id=dados["order_id"],
            charge_id=dados["charge_id"],
            payload_hash=payload_hash,
            recebido_em=agora_utc(),
            status="RECEBIDO",
        )
        db.add(evento)
        db.commit()

        pagamento = None

        if dados["reference_id"]:
            pagamento = (
                db.query(Pagamento)
                .filter(Pagamento.referencia == dados["reference_id"])
                .first()
            )

        if not pagamento and dados["order_id"]:
            pagamento = (
                db.query(Pagamento)
                .filter(Pagamento.pagbank_order_id == dados["order_id"])
                .first()
            )

        # Se for uma notificação de pagamento de pedido conhecido,
        # consulta o pedido diretamente ao PagBank para confirmar o estado.
        dados_confirmados = dados

        if dados["order_id"]:
            pedido_oficial = consultar_pedido(dados["order_id"])
            if pedido_oficial:
                dados_confirmados = extrair_dados_pagbank(pedido_oficial)

        status = str(dados_confirmados.get("status") or "").upper()

        if not pagamento:
            referencia = (
                dados_confirmados.get("reference_id")
                or dados.get("reference_id")
                or f"PAGBANK-{dados.get('order_id') or uuid.uuid4()}"
            )

            valor = dados_confirmados.get("valor")
            valor = int(valor) if valor is not None else 0

            pagamento = Pagamento(
                referencia=referencia[:80],
                cliente_id=None,
                nome_cliente=None,
                usuario_cliente=None,
                valor=valor / 100,
                valor_centavos=valor,
                status="RECEBIDO_SEM_VINCULO",
                metodo="PIX",
                pagbank_order_id=dados_confirmados.get("order_id"),
                pagbank_charge_id=dados_confirmados.get("charge_id"),
                webhook_recebido_em=agora_utc(),
                observacao="Pedido recebido pelo webhook sem cadastro local correspondente.",
                criado_em=agora_utc(),
            )
            db.add(pagamento)
            db.flush()

        pagamento.webhook_recebido_em = agora_utc()

        if status == "PAID":
            processar_pagamento_pago(
                db,
                pagamento,
                dados_confirmados.get("valor"),
            )
        elif status in ("CANCELED", "CANCELLED", "DECLINED"):
            pagamento.status = "CANCELADO"
        elif status == "WAITING":
            pagamento.status = "AGUARDANDO"

        evento.status = "PROCESSADO"
        evento.processado_em = agora_utc()
        evento.detalhe = status

        db.commit()
        return "", 204

    except Exception as exc:
        db.rollback()

        if evento:
            try:
                evento.status = "ERRO"
                evento.detalhe = str(exc)[:1000]
                evento.processado_em = agora_utc()
                db.add(evento)
                db.commit()
            except Exception:
                db.rollback()

        return "Erro interno.", 500

    finally:
        db.close()


# ============================================================
# ADMINISTRADOR DO SISTEMA DE PAGAMENTOS
# Não aparece para os clientes.
# ============================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "")
        senha = request.form.get("senha", "")

        if (
            usuario == ADMIN_USER
            and ADMIN_PASSWORD
            and secrets.compare_digest(senha, ADMIN_PASSWORD)
        ):
            session["admin_logado"] = True
            return redirect(url_for("admin_pagamentos"))

        flash("Login inválido.")

    return render_template_string(
        BASE_CSS + """
        <div class="page">
          <div class="box">
            <div class="brand">
              <div class="icon">🔐</div>
              <h1>Administração</h1>
              <p>Gestão de pagamentos</p>
            </div>
            <div class="card">
              <form method="post">
                <label>Usuário</label>
                <input name="usuario" required>
                <label>Senha</label>
                <input name="senha" type="password" required>
                <button type="submit">Entrar</button>
              </form>
            </div>
          </div>
        </div>
        """
    )


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logado", None)
    return redirect(url_for("admin_login"))


@app.route("/admin/pagamentos")
@admin_required
def admin_pagamentos():
    hoje = date.today()
    inicio_semana = hoje - timedelta(days=hoje.weekday())

    inicio_dt = datetime.combine(inicio_semana, datetime.min.time())
    fim_dt = inicio_dt + timedelta(days=7)

    db = SessionLocal()
    try:
        semana = (
            db.query(Pagamento)
            .filter(
                Pagamento.status == "PAGO",
                Pagamento.pago_em >= inicio_dt,
                Pagamento.pago_em < fim_dt,
            )
            .order_by(Pagamento.pago_em.desc())
            .all()
        )

        total = sum(float(p.valor or 0) for p in semana)

        ultimos = (
            db.query(Pagamento)
            .order_by(Pagamento.criado_em.desc())
            .limit(50)
            .all()
        )

        sem_vinculo = (
            db.query(Pagamento)
            .filter(Pagamento.status == "RECEBIDO_SEM_VINCULO")
            .order_by(Pagamento.pago_em.desc())
            .limit(20)
            .all()
        )

        return render_template_string(
            BASE_CSS + """
            <div class="page">
              <div style="width:min(1100px,100%);margin:auto">
                <div class="brand">
                  <div class="icon">📊</div>
                  <h1>Relatório de Pagamentos</h1>
                  <p>Semana iniciada em {{ inicio }}</p>
                </div>

                <div class="card">
                  <h2>{{ total_formatado }}</h2>
                  <p class="small">{{ quantidade }} pagamentos confirmados nesta semana.</p>
                </div>

                {% if sem_vinculo %}
                <div class="card">
                  <h3>⚠️ Recebidos sem vínculo</h3>
                  <p class="small">
                    Estes pagamentos precisam ser verificados manualmente.
                  </p>
                  <table>
                    <tr>
                      <th>Referência</th>
                      <th>Valor</th>
                      <th>Data</th>
                    </tr>
                    {% for p in sem_vinculo %}
                    <tr>
                      <td>{{ p.referencia }}</td>
                      <td>{{ dinheiro(p.valor) }}</td>
                      <td>{{ p.pago_em or "-" }}</td>
                    </tr>
                    {% endfor %}
                  </table>
                </div>
                {% endif %}

                <div class="card">
                  <h3>Últimos pagamentos</h3>
                  <table>
                    <tr>
                      <th>Cliente</th>
                      <th>Valor</th>
                      <th>Status</th>
                      <th>Data</th>
                    </tr>
                    {% for p in ultimos %}
                    <tr>
                      <td>{{ p.nome_cliente or "-" }}</td>
                      <td>{{ dinheiro(p.valor) }}</td>
                      <td>{{ p.status }}</td>
                      <td>{{ p.criado_em }}</td>
                    </tr>
                    {% endfor %}
                  </table>
                </div>

                <a class="btn btn-secondary" href="{{ url_for('admin_logout') }}">
                  Sair
                </a>
              </div>
            </div>
            """,
            inicio=inicio_semana.strftime("%d/%m/%Y"),
            total_formatado=dinheiro(total),
            quantidade=len(semana),
            ultimos=ultimos,
            sem_vinculo=sem_vinculo,
            dinheiro=dinheiro,
        )
    finally:
        db.close()


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "servico": "Renovação IPTV",
        "pagbank_ambiente": PAGBANK_ENV,
        "public_url_configurada": bool(APP_PUBLIC_URL),
    })


@app.errorhandler(404)
def pagina_404(_):
    return redirect(url_for("pagar"))


# ============================================================
# EXECUÇÃO LOCAL
# ============================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
