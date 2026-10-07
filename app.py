import os
import base64
import secrets
import hashlib
import calendar
from decimal import Decimal, InvalidOperation
from datetime import datetime, date, timedelta
from functools import wraps
from collections import defaultdict

import requests

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    session,
    render_template_string,
    flash,
    jsonify,
    Response,
)

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    Text,
    Boolean,
    func,
    text,
)

from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.exceptions import InvalidSignature


# ============================================================
# CONFIGURAÇÃO
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "change-this-secret-key"
)

DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("DB_URL")
)

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL não configurada no Render."
    )


if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql+psycopg://",
        1
    )

elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+psycopg://",
        1
    )


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=5,
)


Base = declarative_base()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False
)


ADMIN_USER = os.getenv(
    "ADMIN_USER",
    "admin"
)

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD",
    "admin123"
)


# ============================================================
# PAGBANK
# ============================================================

PAGBANK_TOKEN = os.getenv(
    "PAGBANK_TOKEN",
    ""
).strip()

PAGBANK_ENV = os.getenv(
    "PAGBANK_ENV",
    "sandbox"
).strip().lower()

APP_PUBLIC_URL = os.getenv(
    "APP_PUBLIC_URL",
    ""
).strip().rstrip("/")


if PAGBANK_ENV == "production":

    PAGBANK_API = (
        "https://api.pagseguro.com"
    )

else:

    PAGBANK_API = (
        "https://sandbox.api.pagseguro.com"
    )


PAGBANK_TIMEOUT = 25


# ============================================================
# MODELO CLIENTE
# ============================================================

class Cliente(Base):

    __tablename__ = "clientes"

    id = Column(
        Integer,
        primary_key=True
    )

    nome = Column(
        String(150),
        nullable=False
    )

    usuario = Column(
        String(150),
        nullable=False,
        unique=True,
        index=True
    )

    valor = Column(
        Float,
        nullable=False,
        default=0.0
    )

    vencimento = Column(
        Date,
        nullable=True
    )

    status = Column(
        String(20),
        nullable=False,
        default="Pendente",
        index=True
    )

    data_pagamento = Column(
        DateTime,
        nullable=True
    )

    criado_em = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )


# ============================================================
# NOVO MODELO - PAGAMENTOS
# ============================================================

class Pagamento(Base):

    __tablename__ = "pagamentos"

    id = Column(
        Integer,
        primary_key=True
    )

    referencia = Column(
        String(80),
        nullable=False,
        unique=True,
        index=True
    )

    cliente_id = Column(
        Integer,
        nullable=True,
        index=True
    )

    cliente_nome = Column(
        String(150),
        nullable=True
    )

    cliente_usuario = Column(
        String(150),
        nullable=True,
        index=True
    )

    valor = Column(
        Float,
        nullable=False,
        default=0.0
    )

    status = Column(
        String(30),
        nullable=False,
        default="AGUARDANDO",
        index=True
    )

    pagbank_order_id = Column(
        String(100),
        nullable=True,
        unique=True,
        index=True
    )

    pagbank_charge_id = Column(
        String(100),
        nullable=True,
        index=True
    )

    qr_code = Column(
        Text,
        nullable=True
    )

    qr_code_url = Column(
        Text,
        nullable=True
    )

    criado_em = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    pago_em = Column(
        DateTime,
        nullable=True
    )

    vencimento_apos_pagamento = Column(
        Date,
        nullable=True
    )

    webhook_recebido_em = Column(
        DateTime,
        nullable=True
    )

    observacao = Column(
        Text,
        nullable=True
    )


# ============================================================
# MODELO - EVENTOS WEBHOOK
# ============================================================

class WebhookEvent(Base):

    __tablename__ = "webhook_events"

    id = Column(
        Integer,
        primary_key=True
    )

    evento_hash = Column(
        String(128),
        nullable=False,
        unique=True,
        index=True
    )

    order_id = Column(
        String(100),
        nullable=True,
        index=True
    )

    charge_id = Column(
        String(100),
        nullable=True,
        index=True
    )

    recebido_em = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    processado_em = Column(
        DateTime,
        nullable=True
    )

    sucesso = Column(
        Boolean,
        nullable=False,
        default=False
    )

    mensagem = Column(
        Text,
        nullable=True
    )


# ============================================================
# CHAVE PÚBLICA DO WEBHOOK
# ============================================================

class PagBankConfig(Base):

    __tablename__ = "pagbank_config"

    id = Column(
        Integer,
        primary_key=True
    )

    webhook_public_key = Column(
        Text,
        nullable=True
    )

    atualizado_em = Column(
        DateTime,
        nullable=True
    )


Base.metadata.create_all(
    bind=engine
)


# ============================================================
# AUTENTICAÇÃO
# ============================================================

def login_required(view):

    @wraps(view)
    def wrapped(*args, **kwargs):

        if not session.get("logged_in"):

            return redirect(
                url_for("login")
            )

        return view(
            *args,
            **kwargs
        )

    return wrapped


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def dinheiro(valor):

    try:

        numero = float(
            valor or 0
        )

    except Exception:

        numero = 0.0

    return (
        f"R$ {numero:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def formatar_data(valor):

    if not valor:

        return "-"

    if isinstance(valor, datetime):

        return valor.strftime(
            "%d/%m/%Y"
        )

    if isinstance(valor, date):

        return valor.strftime(
            "%d/%m/%Y"
        )

    return str(valor)


def valor_centavos(valor):

    try:

        numero = Decimal(
            str(valor).replace(
                ",",
                "."
            )
        )

        if numero <= 0:

            return None

        return int(
            numero.quantize(
                Decimal("0.01")
            ) * 100
        )

    except (
        InvalidOperation,
        ValueError,
        TypeError
    ):

        return None


def valor_float(valor):

    cents = valor_centavos(
        valor
    )

    if cents is None:

        return None

    return cents / 100


def proximo_dia_10():

    hoje = date.today()

    ano = hoje.year
    mes = hoje.month + 1

    if mes == 13:

        mes = 1
        ano += 1

    return date(
        ano,
        mes,
        10
    )


# ============================================================
# PRÓXIMO VENCIMENTO APÓS PAGAMENTO
# ============================================================

def calcular_proximo_vencimento(
    vencimento_atual
):

    if not vencimento_atual:

        return proximo_dia_10()

    ano = vencimento_atual.year
    mes = vencimento_atual.month

    if mes == 12:

        mes = 1
        ano += 1

    else:

        mes += 1

    return date(
        ano,
        mes,
        10
    )


# ============================================================
# PAGBANK - HEADERS
# ============================================================

def pagbank_headers():

    if not PAGBANK_TOKEN:

        raise RuntimeError(
            "PAGBANK_TOKEN não configurado."
        )

    return {
        "Authorization":
            f"Bearer {PAGBANK_TOKEN}",

        "Accept":
            "application/json",

        "Content-Type":
            "application/json"
    }


# ============================================================
# PAGBANK - CHAVE PÚBLICA
# ============================================================

def obter_chave_publica_pagbank(
    db
):

    config = (
        db.query(
            PagBankConfig
        )
        .first()
    )

    if (
        config
        and config.webhook_public_key
    ):

        return (
            config.webhook_public_key
        )

    resposta = requests.get(
        PAGBANK_API
        + "/public-keys?type=webhook",
        headers={
            "Authorization":
                f"Bearer {PAGBANK_TOKEN}",

            "Accept":
                "application/json"
        },
        timeout=PAGBANK_TIMEOUT
    )

    if resposta.status_code != 200:

        raise RuntimeError(
            "Não foi possível obter "
            "a chave pública do PagBank: "
            + resposta.text[:500]
        )

    dados = resposta.json()

    chave = (
        dados.get(
            "public_key"
        )
    )

    if not chave:

        raise RuntimeError(
            "PagBank não retornou "
            "a chave pública."
        )

    if not config:

        config = PagBankConfig()

        db.add(config)

    config.webhook_public_key = chave
    config.atualizado_em = datetime.utcnow()

    db.commit()

    return chave


# ============================================================
# PAGBANK - VALIDAR WEBHOOK
# ============================================================

def validar_webhook_pagbank(
    corpo_bruto,
    assinatura_header,
    chave_publica
):

    if not assinatura_header:

        return False

    if not chave_publica:

        return False

    assinaturas = [
        item.strip()
        for item in
        assinatura_header.split(",")
        if item.strip()
    ]

    if not assinaturas:

        return False

    try:

        chave_bytes = base64.b64decode(
            chave_publica
        )

        public_key = (
            serialization
            .load_der_public_key(
                chave_bytes
            )
        )

    except Exception:

        return False

    for assinatura in assinaturas:

        try:

            assinatura_bytes = (
                base64.b64decode(
                    assinatura
                )
            )

            public_key.verify(
                assinatura_bytes,
                corpo_bruto,
                ec.ECDSA(
                    hashes.SHA256()
                )
            )

            return True

        except (
            InvalidSignature,
            ValueError,
            TypeError
        ):

            continue

        except Exception:

            continue

    return False


# ============================================================
# PAGBANK - CONSULTAR PEDIDO
# ============================================================

def consultar_pedido_pagbank(
    order_id
):

    resposta = requests.get(
        PAGBANK_API
        + "/orders/"
        + str(order_id),

        headers=pagbank_headers(),

        timeout=PAGBANK_TIMEOUT
    )

    if resposta.status_code != 200:

        raise RuntimeError(
            "Não foi possível consultar "
            "o pedido PagBank: "
            + resposta.text[:500]
        )

    return resposta.json()


# ============================================================
# PAGBANK - CRIAR PIX
# ============================================================

def criar_pix_pagbank(
    pagamento
):

    if not APP_PUBLIC_URL:

        raise RuntimeError(
            "APP_PUBLIC_URL não configurada."
        )

    referencia = (
        pagamento.referencia
    )

    centavos = valor_centavos(
        pagamento.valor
    )

    if centavos is None:

        raise RuntimeError(
            "Valor inválido."
        )

    expiration = (
        datetime.utcnow()
        + timedelta(minutes=30)
    )

    expiration_text = (
        expiration.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )

    payload = {

        "reference_id":
            referencia,

        "items": [

            {
                "reference_id":
                    referencia,

                "name":
                    "Mensalidade IPTV",

                "quantity":
                    1,

                "unit_amount":
                    centavos
            }

        ],

        "notification_urls": [

            APP_PUBLIC_URL
            + "/webhooks/pagbank"

        ],

        "charges": [

            {

                "reference_id":
                    referencia,

                "description":
                    "Pagamento de mensalidade IPTV",

                "amount": {

                    "value":
                        centavos,

                    "currency":
                        "BRL"

                },

                "payment_method": {

                    "type":
                        "PIX",

                    "pix": {

                        "expiration_date":
                            expiration_text

                    }

                }

            }

        ]

    }

    resposta = requests.post(
        PAGBANK_API
        + "/orders",

        headers=pagbank_headers(),

        json=payload,

        timeout=PAGBANK_TIMEOUT
    )

    if resposta.status_code not in (
        200,
        201
    ):

        raise RuntimeError(
            "Erro ao criar Pix no PagBank: "
            + resposta.text[:1000]
        )

    dados = resposta.json()

    charges = (
        dados.get(
            "charges"
        )
        or []
    )

    if not charges:

        raise RuntimeError(
            "PagBank não retornou a cobrança."
        )

    charge = charges[0]

    qr = (
        charge.get(
            "qr_code"
        )
        or {}
    )

    pagamento.pagbank_order_id = (
        dados.get("id")
    )

    pagamento.pagbank_charge_id = (
        charge.get("id")
    )

    pagamento.qr_code = (
        qr.get("text")
    )

    links = (
        charge.get("links")
        or []
    )

    qr_url = None

    for link in links:

        if link.get(
            "rel"
        ) == "QRCODE.PNG":

            qr_url = link.get(
                "href"
            )

            break

    pagamento.qr_code_url = qr_url

    pagamento.status = (
        "AGUARDANDO"
    )

    return dados


# ============================================================
# RESUMO
# ============================================================

def obter_resumo(db):

    total = (
        db.query(
            func.count(
                Cliente.id
            )
        ).scalar()
        or 0
    )

    pagos = (
        db.query(
            func.count(
                Cliente.id
            )
        )
        .filter(
            Cliente.status == "Pago"
        )
        .scalar()
        or 0
    )

    pendentes = (
        db.query(
            func.count(
                Cliente.id
            )
        )
        .filter(
            Cliente.status == "Pendente"
        )
        .scalar()
        or 0
    )

    recebido = (
        db.query(
            func.coalesce(
                func.sum(
                    Cliente.valor
                ),
                0
            )
        )
        .filter(
            Cliente.status == "Pago"
        )
        .scalar()
        or 0
    )

    pendente_valor = (
        db.query(
            func.coalesce(
                func.sum(
                    Cliente.valor
                ),
                0
            )
        )
        .filter(
            Cliente.status == "Pendente"
        )
        .scalar()
        or 0
    )

    recebido = float(
        recebido
    )

    pendente_valor = float(
        pendente_valor
    )

    previsto = (
        recebido
        + pendente_valor
    )

    percentual = (
        (pagos / total) * 100
        if total
        else 0
    )

    return {
        "total": total,
        "pagos": pagos,
        "pendentes": pendentes,
        "recebido": recebido,
        "pendente_valor":
            pendente_valor,
        "previsto": previsto,
        "percentual":
            percentual,
    }


# ============================================================
# GRAFICO
# ============================================================

def obter_grafico_mensal(db):

    clientes = (
        db.query(Cliente)
        .filter(
            Cliente.status == "Pago",
            Cliente.data_pagamento.isnot(None)
        )
        .order_by(
            Cliente.data_pagamento.asc()
        )
        .all()
    )

    meses = defaultdict(float)

    nomes_meses = [
        "Jan",
        "Fev",
        "Mar",
        "Abr",
        "Mai",
        "Jun",
        "Jul",
        "Ago",
        "Set",
        "Out",
        "Nov",
        "Dez",
    ]

    for cliente in clientes:

        if not cliente.data_pagamento:

            continue

        chave = (
            cliente.data_pagamento.year,
            cliente.data_pagamento.month
        )

        meses[chave] += float(
            cliente.valor or 0
        )

    hoje = date.today()

    resultado = []

    for i in range(5, -1, -1):

        ano = hoje.year
        mes = hoje.month - i

        while mes <= 0:

            mes += 12
            ano -= 1

        valor = meses.get(
            (ano, mes),
            0
        )

        resultado.append({
            "label":
                f"{nomes_meses[mes - 1]}"
                f"/{str(ano)[-2:]}",

            "valor":
                round(valor, 2)
        })

    maior = max(
        [
            x["valor"]
            for x in resultado
        ]
        or [1]
    )

    for item in resultado:

        item["percentual"] = (
            (
                item["valor"]
                / maior
            ) * 100
            if maior > 0
            else 0
        )

    return resultado


def obter_clientes_recentes(db):

    return (
        db.query(Cliente)
        .order_by(
            Cliente.criado_em.desc()
        )
        .limit(5)
        .all()
    )


# ============================================================
# DESIGN
# ============================================================

BASE = r"""
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>
    {{ title }} · IPTV Manager
</title>

<style>

:root {
    --bg:#070b12;
    --bg2:#0b1220;
    --panel:#111827;
    --panel2:#151e2d;
    --border:#243044;
    --text:#f8fafc;
    --muted:#94a3b8;
    --blue:#3b82f6;
    --blue2:#2563eb;
    --green:#22c55e;
    --yellow:#facc15;
    --red:#ef4444;
    --cyan:#06b6d4;
    --shadow:0 18px 50px rgba(0,0,0,.25);
}

* {
    box-sizing:border-box;
}

html {
    scroll-behavior:smooth;
}

body {
    margin:0;
    background:
        radial-gradient(
            circle at 10% 0%,
            rgba(37,99,235,.13),
            transparent 30%
        ),
        radial-gradient(
            circle at 90% 10%,
            rgba(6,182,212,.08),
            transparent 25%
        ),
        linear-gradient(
            135deg,
            #060910,
            #0b1220
        );
    color:var(--text);
    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
}

a {
    text-decoration:none;
    color:inherit;
}

button,
input,
select {
    font:inherit;
}

.layout {
    min-height:100vh;
    display:flex;
}

.sidebar {
    width:255px;
    background:rgba(8,13,22,.94);
    border-right:1px solid var(--border);
    padding:22px 15px;
    position:fixed;
    inset:0 auto 0 0;
    z-index:100;
    backdrop-filter:blur(18px);
}

.brand {
    display:flex;
    align-items:center;
    gap:12px;
    padding:7px 10px 25px;
}

.brand-icon {
    width:45px;
    height:45px;
    border-radius:13px;
    background:linear-gradient(
        135deg,
        #2563eb,
        #06b6d4
    );
    display:grid;
    place-items:center;
    font-size:22px;
    box-shadow:
        0 8px 25px
        rgba(37,99,235,.25);
}

.brand strong {
    display:block;
    font-size:18px;
    font-weight:850;
}

.brand span {
    display:block;
    font-size:11px;
    color:var(--muted);
    margin-top:2px;
}

.nav-title {
    font-size:10px;
    color:#64748b;
    text-transform:uppercase;
    letter-spacing:1.3px;
    padding:0 12px 8px;
}

.nav a {
    display:flex;
    align-items:center;
    gap:10px;
    padding:12px;
    border-radius:11px;
    color:#cbd5e1;
    margin:4px 0;
    transition:.2s;
}

.nav a:hover {
    background:rgba(255,255,255,.05);
    color:white;
    transform:translateX(2px);
}

.nav a.active {
    background:
        linear-gradient(
            90deg,
            rgba(37,99,235,.25),
            rgba(37,99,235,.08)
        );
    color:white;
    border:
        1px solid
        rgba(59,130,246,.25);
}

.sidebar-footer {
    position:absolute;
    left:15px;
    right:15px;
    bottom:20px;
}

.logout {
    display:block;
    text-align:center;
    padding:11px;
    border:1px solid var(--border);
    border-radius:11px;
    color:#cbd5e1;
}

.main {
    margin-left:255px;
    width:calc(100% - 255px);
    min-height:100vh;
    padding:30px;
    max-width:1600px;
}

.top {
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:15px;
    margin-bottom:25px;
}

h1 {
    font-size:30px;
    margin:0 0 5px;
    font-weight:850;
}

h2,
h3 {
    margin-top:0;
}

.subtitle {
    color:var(--muted);
    font-size:13px;
}

.grid {
    display:grid;
    grid-template-columns:repeat(4,1fr);
    gap:16px;
    margin-bottom:18px;
}

.grid2 {
    display:grid;
    grid-template-columns:repeat(2,1fr);
    gap:18px;
}

.card {
    background:
        linear-gradient(
            145deg,
            rgba(17,24,39,.94),
            rgba(13,20,32,.94)
        );
    border:1px solid var(--border);
    border-radius:17px;
    padding:20px;
    box-shadow:var(--shadow);
}

.metric-label {
    color:var(--muted);
    font-size:12px;
}

.metric {
    font-size:27px;
    font-weight:850;
    margin-top:8px;
}

.metric-small {
    color:var(--muted);
    font-size:12px;
    margin-top:5px;
}

.green { color:var(--green); }
.yellow { color:var(--yellow); }
.red { color:var(--red); }
.blue { color:#60a5fa; }
.cyan { color:#22d3ee; }

.toolbar {
    display:flex;
    gap:10px;
    flex-wrap:wrap;
    margin-bottom:18px;
}

input,
select {
    width:100%;
    background:#0a111e;
    color:white;
    border:1px solid var(--border);
    border-radius:10px;
    padding:12px;
    outline:none;
}

input:focus,
select:focus {
    border-color:var(--blue);
    box-shadow:
        0 0 0 3px
        rgba(59,130,246,.10);
}

.form-grid {
    display:grid;
    grid-template-columns:repeat(2,1fr);
    gap:16px;
}

label {
    display:block;
    color:#cbd5e1;
    font-size:13px;
    margin-bottom:7px;
    font-weight:650;
}

.btn {
    border:0;
    border-radius:10px;
    padding:10px 14px;
    font-weight:750;
    cursor:pointer;
    display:inline-flex;
    align-items:center;
    justify-content:center;
    gap:6px;
}

.primary {
    background:
        linear-gradient(
            135deg,
            #3b82f6,
            #2563eb
        );
    color:white;
}

.secondary {
    background:#172236;
    color:white;
    border:1px solid var(--border);
}

.success {
    background:rgba(34,197,94,.14);
    color:#86efac;
    border:1px solid rgba(34,197,94,.20);
}

.danger {
    background:rgba(239,68,68,.12);
    color:#fecaca;
    border:1px solid rgba(239,68,68,.20);
}

.warning {
    background:rgba(250,204,21,.12);
    color:#fde68a;
}

.full {
    width:100%;
}

.mt {
    margin-top:16px;
}

.actions {
    display:flex;
    gap:7px;
    flex-wrap:wrap;
}

.flash {
    padding:13px 16px;
    border-radius:11px;
    background:rgba(37,99,235,.12);
    border:1px solid rgba(59,130,246,.25);
    margin-bottom:18px;
    color:#bfdbfe;
}

.client {
    display:grid;
    grid-template-columns:2fr 1fr 1fr 1fr auto;
    gap:15px;
    align-items:center;
    padding:16px;
    border:1px solid var(--border);
    border-radius:15px;
    background:rgba(16,24,39,.82);
    margin-bottom:10px;
}

.name {
    font-size:16px;
    font-weight:850;
}

.username {
    font-size:12px;
    color:var(--muted);
    margin-top:3px;
}

.badge {
    display:inline-flex;
    align-items:center;
    padding:6px 10px;
    border-radius:999px;
    font-size:11px;
    font-weight:850;
}

.badge.paid {
    background:rgba(34,197,94,.13);
    color:#4ade80;
}

.badge.pending {
    background:rgba(250,204,21,.13);
    color:#fde047;
}

.badge.waiting {
    background:rgba(59,130,246,.13);
    color:#93c5fd;
}

.drawer {
    margin-top:9px;
    border:1px solid var(--border);
    border-radius:12px;
    overflow:hidden;
    background:rgba(8,13,22,.55);
}

.drawer summary {
    cursor:pointer;
    padding:11px 13px;
    color:#cbd5e1;
    font-size:12px;
    font-weight:700;
}

.drawer-content {
    padding:14px;
    border-top:1px solid var(--border);
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:12px;
}

.detail-box {
    padding:11px;
    border-radius:10px;
    background:#0b1220;
}

.detail-label {
    color:var(--muted);
    font-size:10px;
    text-transform:uppercase;
}

.detail-value {
    margin-top:4px;
    font-weight:750;
    font-size:13px;
}

.chart-card {
    min-height:280px;
}

.chart-bars {
    height:190px;
    display:flex;
    align-items:end;
    gap:14px;
    padding:20px 5px 5px;
}

.chart-column {
    flex:1;
    height:100%;
    display:flex;
    flex-direction:column;
    justify-content:end;
    align-items:center;
    gap:7px;
}

.chart-bar {
    width:100%;
    max-width:48px;
    min-height:4px;
    border-radius:8px 8px 3px 3px;
    background:
        linear-gradient(
            180deg,
            #60a5fa,
            #2563eb
        );
}

.chart-value {
    font-size:10px;
    color:#cbd5e1;
}

.chart-label {
    font-size:10px;
    color:var(--muted);
}

.progress-wrap {
    margin-top:15px;
}

.progress {
    width:100%;
    height:11px;
    background:#1e293b;
    border-radius:99px;
    overflow:hidden;
}

.progress span {
    display:block;
    height:100%;
    background:
        linear-gradient(
            90deg,
            #22c55e,
            #3b82f6
        );
    border-radius:99px;
}

.financial {
    display:grid;
    gap:18px;
}

.financial-row {
    display:grid;
    grid-template-columns:90px 1fr 105px;
    align-items:center;
    gap:10px;
}

.financial-track {
    height:13px;
    background:#1e293b;
    border-radius:99px;
    overflow:hidden;
}

.financial-fill {
    height:100%;
    border-radius:99px;
}

.financial-green {
    background:
        linear-gradient(
            90deg,
            #16a34a,
            #4ade80
        );
}

.financial-yellow {
    background:
        linear-gradient(
            90deg,
            #ca8a04,
            #fde047
        );
}

.recent {
    display:grid;
    gap:9px;
}

.recent-item {
    display:flex;
    justify-content:space-between;
    align-items:center;
    gap:10px;
    padding:12px;
    border-radius:11px;
    background:#0b1220;
    border:1px solid var(--border);
}

.recent-name {
    font-weight:750;
    font-size:13px;
}

.recent-user {
    color:var(--muted);
    font-size:11px;
    margin-top:2px;
}

table {
    width:100%;
    border-collapse:collapse;
}

th,
td {
    text-align:left;
    padding:12px;
    border-bottom:1px solid var(--border);
    font-size:13px;
}

th {
    color:#94a3b8;
    font-weight:650;
}

.empty {
    text-align:center;
    padding:45px;
    color:var(--muted);
}

.pix-box {
    text-align:center;
    max-width:500px;
    margin:auto;
}

.pix-qrcode {
    width:280px;
    max-width:100%;
    background:white;
    padding:10px;
    border-radius:15px;
    margin:15px auto;
    display:block;
}

.pix-code {
    width:100%;
    min-height:100px;
    resize:vertical;
    font-size:12px;
}

.payment-success {
    padding:25px;
    border-radius:15px;
    background:rgba(34,197,94,.10);
    border:1px solid rgba(34,197,94,.30);
    text-align:center;
}

.payment-waiting {
    padding:25px;
    border-radius:15px;
    background:rgba(59,130,246,.10);
    border:1px solid rgba(59,130,246,.30);
    text-align:center;
}

.mobile-menu {
    display:none;
}

@media(max-width:1100px) {

    .grid {
        grid-template-columns:repeat(2,1fr);
    }

    .client {
        grid-template-columns:1fr 1fr;
    }
}

@media(max-width:800px) {

    .sidebar {
        width:220px;
        transform:translateX(-100%);
        transition:.25s;
    }

    .sidebar.open {
        transform:translateX(0);
    }

    .mobile-menu {
        display:inline-flex;
        position:fixed;
        top:14px;
        left:14px;
        z-index:200;
        width:42px;
        height:42px;
        border-radius:11px;
        border:1px solid var(--border);
        background:#111827;
        color:white;
        align-items:center;
        justify-content:center;
        cursor:pointer;
    }

    .main {
        margin-left:0;
        width:100%;
        padding:65px 16px 25px;
    }

    .grid,
    .grid2,
    .form-grid {
        grid-template-columns:1fr;
    }

    .top {
        align-items:flex-start;
        flex-direction:column;
    }

    .client {
        grid-template-columns:1fr;
    }

    .drawer-content {
        grid-template-columns:1fr;
    }

    .financial-row {
        grid-template-columns:75px 1fr 90px;
    }
}

</style>

</head>

<body>

<button
    class="mobile-menu"
    onclick="
        document
        .querySelector('.sidebar')
        .classList
        .toggle('open')
    "
>
    ☰
</button>

<div class="layout">

<aside class="sidebar">

<div class="brand">

<div class="brand-icon">
    📺
</div>

<div>

<strong>
    IPTV Manager
</strong>

<span>
    Administração
</span>

</div>

</div>

<div class="nav-title">
    Menu principal
</div>

<nav class="nav">

<a
    class="{{ 'active' if active=='dashboard' else '' }}"
    href="{{ url_for('dashboard') }}"
>
    📊
    <span>Dashboard</span>
</a>

<a
    class="{{ 'active' if active=='clientes' else '' }}"
    href="{{ url_for('clientes') }}"
>
    👥
    <span>Clientes</span>
</a>

<a
    class="{{ 'active' if active=='novo' else '' }}"
    href="{{ url_for('novo_cliente') }}"
>
    ➕
    <span>Adicionar cliente</span>
</a>

<a
    class="{{ 'active' if active=='importar' else '' }}"
    href="{{ url_for('importar') }}"
>
    📥
    <span>Importar clientes</span>
</a>

<a
    class="{{ 'active' if active=='pagamentos' else '' }}"
    href="{{ url_for('pagamentos_admin') }}"
>
    💳
    <span>Pagamentos Pix</span>
</a>

<a
    class="{{ 'active' if active=='relatorios' else '' }}"
    href="{{ url_for('relatorios') }}"
>
    📈
    <span>Relatórios</span>
</a>

</nav>

<div class="sidebar-footer">

<a
    class="logout"
    href="{{ url_for('logout') }}"
>
    🚪 Sair do sistema
</a>

</div>

</aside>

<main class="main">

{% with messages = get_flashed_messages() %}

{% for message in messages %}

<div class="flash">
    {{ message }}
</div>

{% endfor %}

{% endwith %}

{{ content | safe }}

</main>

</div>

</body>

</html>
"""


# ============================================================
# LOGIN
# ============================================================

LOGIN = r"""
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
    Login · IPTV Manager
</title>

<style>

* {
    box-sizing:border-box;
}

body {
    margin:0;
    min-height:100vh;
    display:grid;
    place-items:center;
    padding:20px;
    background:
        radial-gradient(
            circle at 10% 10%,
            rgba(37,99,235,.18),
            transparent 35%
        ),
        linear-gradient(
            135deg,
            #060910,
            #0b1220
        );
    color:white;
    font-family:
        Inter,
        system-ui,
        sans-serif;
}

.login {
    width:min(420px,100%);
    background:rgba(17,24,39,.96);
    border:1px solid #243044;
    border-radius:22px;
    padding:32px;
    box-shadow:
        0 25px 80px
        rgba(0,0,0,.40);
}

.icon {
    width:60px;
    height:60px;
    margin:auto;
    border-radius:16px;
    display:grid;
    place-items:center;
    background:
        linear-gradient(
            135deg,
            #2563eb,
            #06b6d4
        );
    font-size:29px;
}

h1 {
    text-align:center;
    margin:15px 0 5px;
    font-size:26px;
}

.sub {
    text-align:center;
    color:#94a3b8;
    margin-bottom:25px;
    font-size:13px;
}

label {
    display:block;
    color:#cbd5e1;
    font-size:13px;
    margin:13px 0 7px;
}

input {
    width:100%;
    padding:13px;
    border-radius:10px;
    border:1px solid #243044;
    background:#0b1220;
    color:white;
    font-size:15px;
    outline:none;
}

button {
    width:100%;
    margin-top:18px;
    padding:13px;
    border:0;
    border-radius:10px;
    background:
        linear-gradient(
            135deg,
            #3b82f6,
            #2563eb
        );
    color:white;
    font-weight:800;
    font-size:15px;
    cursor:pointer;
}

.error {
    background:rgba(239,68,68,.12);
    color:#fecaca;
    border:1px solid rgba(239,68,68,.20);
    padding:11px;
    border-radius:9px;
    margin-bottom:12px;
    font-size:13px;
}

</style>

</head>

<body>

<div class="login">

<div class="icon">
    📺
</div>

<h1>
    IPTV Manager
</h1>

<div class="sub">
    Painel administrativo
</div>

{% if error %}

<div class="error">
    {{ error }}
</div>

{% endif %}

<form method="post">

<label>
    Usuário
</label>

<input
    name="usuario"
    autocomplete="username"
    required
    placeholder="Digite seu usuário"
>

<label>
    Senha
</label>

<input
    type="password"
    name="senha"
    autocomplete="current-password"
    required
    placeholder="Digite sua senha"
>

<button>
    Entrar no sistema
</button>

</form>

</div>

</body>

</html>
"""


# ============================================================
# RENDERIZAÇÃO
# ============================================================

def page(
    content,
    title,
    active,
    **context
):

    context["dinheiro"] = dinheiro
    context["formatar_data"] = formatar_data

    rendered_content = render_template_string(
        content,
        **context
    )

    return render_template_string(
        BASE,
        content=rendered_content,
        title=title,
        active=active
    )


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["GET", "POST"]
)
def login():

    if request.method == "POST":

        usuario = request.form.get(
            "usuario",
            ""
        ).strip()

        senha = request.form.get(
            "senha",
            ""
        )

        if (
            usuario == ADMIN_USER
            and senha == ADMIN_PASSWORD
        ):

            session["logged_in"] = True

            return redirect(
                url_for("dashboard")
            )

        return render_template_string(
            LOGIN,
            error=
                "Usuário ou senha incorretos."
        )

    return render_template_string(
        LOGIN,
        error=None
    )


@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


@app.route("/")
def index():

    if session.get("logged_in"):

        return redirect(
            url_for("dashboard")
        )

    return redirect(
        url_for("login")
    )


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():

    db = SessionLocal()

    try:

        resumo = obter_resumo(db)

        grafico_mensal = (
            obter_grafico_mensal(db)
        )

        recentes = (
            obter_clientes_recentes(db)
        )

        content = r"""

<div class="top">

<div>

<h1>
    Dashboard
</h1>

<div class="subtitle">
    Visão geral da sua operação
</div>

</div>

<div class="actions">

<a
    class="btn secondary"
    href="{{ url_for('clientes') }}"
>
    👥 Ver clientes
</a>

<a
    class="btn primary"
    href="{{ url_for('novo_cliente') }}"
>
    ＋ Novo cliente
</a>

</div>

</div>

<div class="grid">

<div class="card">

<div class="metric-label">
    👥 Total de clientes
</div>

<div class="metric">
    {{ resumo.total }}
</div>

<div class="metric-small">
    Clientes cadastrados
</div>

</div>

<div class="card">

<div class="metric-label">
    🟢 Clientes pagos
</div>

<div class="metric green">
    {{ resumo.pagos }}
</div>

<div class="metric-small">
    {{ "%.1f"|format(resumo.percentual) }}% da base
</div>

</div>

<div class="card">

<div class="metric-label">
    🟡 Pendentes
</div>

<div class="metric yellow">
    {{ resumo.pendentes }}
</div>

<div class="metric-small">
    Aguardando pagamento
</div>

</div>

<div class="card">

<div class="metric-label">
    💰 Total recebido
</div>

<div class="metric blue">
    {{ dinheiro(resumo.recebido) }}
</div>

<div class="metric-small">
    Pagamentos confirmados
</div>

</div>

</div>

<div class="grid2">

<div class="card chart-card">

<h3>
    📈 Recebimentos
</h3>

<div class="subtitle">
    Evolução dos pagamentos registrados
</div>

<div class="chart-bars">

{% for item in grafico_mensal %}

<div class="chart-column">

<div class="chart-value">
    {{ dinheiro(item.valor) }}
</div>

<div
    class="chart-bar"
    style="
        height:
        {{ item.percentual if item.percentual > 2 else 2 }}%;
    "
></div>

<div class="chart-label">
    {{ item.label }}
</div>

</div>

{% endfor %}

</div>

</div>

<div class="card">

<h3>
    💰 Resumo financeiro
</h3>

<div class="subtitle">
    Situação atual da receita
</div>

<div class="financial" style="margin-top:25px;">

{% set recebido_percentual =
    (resumo.recebido / resumo.previsto * 100)
    if resumo.previsto
    else 0
%}

{% set pendente_percentual =
    (resumo.pendente_valor / resumo.previsto * 100)
    if resumo.previsto
    else 0
%}

<div class="financial-row">

<strong>
    Recebido
</strong>

<div class="financial-track">

<div
    class="financial-fill financial-green"
    style="width:{{ recebido_percentual }}%;"
></div>

</div>

<strong class="green">
    {{ dinheiro(resumo.recebido) }}
</strong>

</div>

<div class="financial-row">

<strong>
    Pendente
</strong>

<div class="financial-track">

<div
    class="financial-fill financial-yellow"
    style="width:{{ pendente_percentual }}%;"
></div>

</div>

<strong class="yellow">
    {{ dinheiro(resumo.pendente_valor) }}
</strong>

</div>

</div>

<div class="progress-wrap">

<div style="
    display:flex;
    justify-content:space-between;
    font-size:12px;
    color:#94a3b8;
">

<span>
    Pagamentos recebidos
</span>

<strong>
    {{ "%.1f"|format(resumo.percentual) }}%
</strong>

</div>

<div class="progress">

<span
    style="
        width:{{ resumo.percentual }}%;
    "
></span>

</div>

</div>

</div>

</div>

<div
    class="grid2"
    style="margin-top:18px;"
>

<div class="card">

<h3>
    🆕 Clientes recentes
</h3>

<div class="recent" style="margin-top:15px;">

{% for cliente in recentes %}

<div class="recent-item">

<div>

<div class="recent-name">
    {{ cliente.nome }}
</div>

<div class="recent-user">
    {{ cliente.usuario }}
</div>

</div>

<span
    class="badge
    {{ 'paid'
       if cliente.status == 'Pago'
       else 'pending' }}"
>

{{ cliente.status }}

</span>

</div>

{% else %}

<div class="empty">
    Nenhum cliente cadastrado.
</div>

{% endfor %}

</div>

</div>

<div class="card">

<h3>
    ⚡ Ações rápidas
</h3>

<div
    style="
        display:grid;
        grid-template-columns:1fr 1fr;
        gap:10px;
        margin-top:18px;
    "
>

<a
    class="btn primary"
    href="{{ url_for('novo_cliente') }}"
>
    ➕ Cadastrar
</a>

<a
    class="btn secondary"
    href="{{ url_for('clientes') }}"
>
    👥 Clientes
</a>

<a
    class="btn secondary"
    href="{{ url_for('pagamentos_admin') }}"
>
    💳 Pagamentos
</a>

<a
    class="btn secondary"
    href="{{ url_for('relatorios') }}"
>
    📈 Relatórios
</a>

</div>

</div>

</div>

"""

        return page(
            content,
            "Dashboard",
            "dashboard",
            resumo=resumo,
            grafico_mensal=grafico_mensal,
            recentes=recentes
        )

    finally:

        db.close()


# ============================================================
# CLIENTES
# ============================================================

@app.route("/clientes")
@login_required
def clientes():

    db = SessionLocal()

    try:

        busca = request.args.get(
            "busca",
            ""
        ).strip()

        status = request.args.get(
            "status",
            "Todos"
        )

        query = db.query(
            Cliente
        )

        if busca:

            termo = f"%{busca}%"

            query = query.filter(
                (
                    Cliente.nome.ilike(
                        termo
                    )
                )
                |
                (
                    Cliente.usuario.ilike(
                        termo
                    )
                )
            )

        if status in (
            "Pago",
            "Pendente"
        ):

            query = query.filter(
                Cliente.status == status
            )

        lista = (
            query
            .order_by(
                Cliente.nome.asc()
            )
            .all()
        )

        content = r"""

<div class="top">

<div>

<h1>
    Clientes
</h1>

<div class="subtitle">
    Gerencie clientes, pagamentos e usuários.
</div>

</div>

<a
    class="btn primary"
    href="{{ url_for('novo_cliente') }}"
>
    ＋ Novo cliente
</a>

</div>

<div class="card" style="margin-bottom:18px;">

<form method="get" class="toolbar">

<div style="flex:2;min-width:220px;">

<label>
    Pesquisar cliente
</label>

<input
    name="busca"
    value="{{ busca }}"
    placeholder="Nome ou usuário..."
>

</div>

<div style="flex:1;min-width:160px;">

<label>
    Status
</label>

<select name="status">

<option
    value="Todos"
    {{ 'selected' if status == 'Todos' }}
>
    Todos
</option>

<option
    value="Pago"
    {{ 'selected' if status == 'Pago' }}
>
    Pagos
</option>

<option
    value="Pendente"
    {{ 'selected' if status == 'Pendente' }}
>
    Pendentes
</option>

</select>

</div>

<div style="align-self:end;">

<button
    class="btn primary"
    type="submit"
>
    🔎 Filtrar
</button>

</div>

</form>

<div
    style="
        color:#94a3b8;
        font-size:12px;
    "
>

{{ lista|length }}
cliente(s) encontrado(s)

</div>

</div>

{% if lista %}

{% for c in lista %}

<div class="client">

<div>

<div class="name">
    {{ c.nome }}
</div>

<div class="username">
    {{ c.usuario }}
</div>

<details class="drawer">

<summary>
    ▾ Ver detalhes
</summary>

<div class="drawer-content">

<div class="detail-box">

<div class="detail-label">
    Valor
</div>

<div class="detail-value">
    {{ dinheiro(c.valor) }}
</div>

</div>

<div class="detail-box">

<div class="detail-label">
    Vencimento
</div>

<div class="detail-value">
    {{ formatar_data(c.vencimento) }}
</div>

</div>

<div class="detail-box">

<div class="detail-label">
    Pagamento
</div>

<div class="detail-value">
    {{ formatar_data(c.data_pagamento) }}
</div>

</div>

</div>

</details>

</div>

<div>

<div class="metric-label">
    Mensalidade
</div>

<strong>
    {{ dinheiro(c.valor) }}
</strong>

</div>

<div>

<div class="metric-label">
    Vencimento
</div>

<strong>
    {{ formatar_data(c.vencimento) }}
</strong>

</div>

<div>

<span
    class="badge
    {{ 'paid'
       if c.status == 'Pago'
       else 'pending' }}"
>

{{ '✓ Pago'
   if c.status == 'Pago'
   else '⏳ Pendente' }}

</span>

</div>

<div class="actions">

<form
    method="post"
    action="{{ url_for(
        'alternar_status',
        cliente_id=c.id
    ) }}"
>

<button
    class="btn
    {{ 'secondary'
       if c.status == 'Pago'
       else 'success' }}"
    type="submit"
>

{{ 'Marcar pendente'
   if c.status == 'Pago'
   else 'Marcar pago' }}

</button>

</form>

<a
    class="btn secondary"
    href="{{ url_for(
        'editar_cliente',
        cliente_id=c.id
    ) }}"
>
    Editar
</a>

<form
    method="post"
    action="{{ url_for(
        'excluir_cliente',
        cliente_id=c.id
    ) }}"
    onsubmit="
        return confirm(
            'Excluir este cliente definitivamente?'
        );
    "
>

<button
    class="btn danger"
    type="submit"
>
    Excluir
</button>

</form>

</div>

</div>

{% endfor %}

{% else %}

<div class="card empty">

<div style="font-size:35px;">
    👥
</div>

<h3>
    Nenhum cliente encontrado
</h3>

</div>

{% endif %}

"""

        return page(
            content,
            "Clientes",
            "clientes",
            lista=lista,
            busca=busca,
            status=status
        )

    finally:

        db.close()


# ============================================================
# FORMULÁRIO
# ============================================================

FORM = r"""

<div class="top">

<div>

<h1>
    {{ 'Editar cliente'
       if editar
       else 'Adicionar cliente' }}
</h1>

<div class="subtitle">

{{ 'Atualize os dados do cliente.'
   if editar
   else 'Cadastre um novo cliente.' }}

</div>

</div>

<a
    class="btn secondary"
    href="{{ url_for('clientes') }}"
>
    ← Voltar
</a>

</div>

<div class="card">

<form method="post">

<div class="form-grid">

<div>

<label>
    Nome do cliente
</label>

<input
    name="nome"
    value="{{ c.nome if c else '' }}"
    required
    maxlength="150"
    placeholder="Ex.: João Silva"
>

</div>

<div>

<label>
    Nome de usuário
</label>

<input
    name="usuario"
    value="{{ c.usuario if c else '' }}"
    required
    maxlength="150"
    placeholder="Ex.: joao123"
>

</div>

<div>

<label>
    Valor da mensalidade
</label>

<input
    type="number"
    step="0.01"
    min="0"
    name="valor"
    value="{{ c.valor if c else '25.00' }}"
    required
>

</div>

<div>

<label>
    Data de vencimento
</label>

<input
    type="date"
    name="vencimento"
    value="{{
        c.vencimento.isoformat()
        if c and c.vencimento
        else ''
    }}"
>

</div>

</div>

<div class="actions mt">

<button
    class="btn primary"
    type="submit"
>

{{ '💾 Salvar alterações'
   if editar
   else '✓ Cadastrar cliente' }}

</button>

<a
    class="btn secondary"
    href="{{ url_for('clientes') }}"
>
    Cancelar
</a>

</div>

</form>

</div>

"""


# ============================================================
# NOVO CLIENTE
# ============================================================

@app.route(
    "/clientes/novo",
    methods=["GET", "POST"]
)
@login_required
def novo_cliente():

    if request.method == "POST":

        db = SessionLocal()

        try:

            nome = request.form.get(
                "nome",
                ""
            ).strip()

            usuario = request.form.get(
                "usuario",
                ""
            ).strip()

            valor = valor_float(
                request.form.get(
                    "valor",
                    "0"
                )
            )

            vencimento_texto = (
                request.form.get(
                    "vencimento",
                    ""
                ).strip()
            )

            vencimento = (
                date.fromisoformat(
                    vencimento_texto
                )
                if vencimento_texto
                else None
            )

            if not nome or not usuario:

                flash(
                    "Nome e usuário são obrigatórios."
                )

                return redirect(
                    url_for("novo_cliente")
                )

            if valor is None:

                flash(
                    "Valor inválido."
                )

                return redirect(
                    url_for("novo_cliente")
                )

            existente = (
                db.query(Cliente)
                .filter(
                    Cliente.usuario
                    == usuario
                )
                .first()
            )

            if existente:

                flash(
                    "Esse nome de usuário já está cadastrado."
                )

                return redirect(
                    url_for("novo_cliente")
                )

            cliente = Cliente(
                nome=nome,
                usuario=usuario,
                valor=valor,
                vencimento=vencimento,
                status="Pendente"
            )

            db.add(cliente)
            db.commit()

            flash(
                "Cliente cadastrado com sucesso."
            )

            return redirect(
                url_for("clientes")
            )

        except Exception:

            db.rollback()

            flash(
                "Não foi possível cadastrar o cliente."
            )

            return redirect(
                url_for("novo_cliente")
            )

        finally:

            db.close()

    return page(
        FORM,
        "Adicionar cliente",
        "novo",
        c=None,
        editar=False
    )


# ============================================================
# EDITAR CLIENTE
# ============================================================

@app.route(
    "/clientes/<int:cliente_id>/editar",
    methods=["GET", "POST"]
)
@login_required
def editar_cliente(
    cliente_id
):

    db = SessionLocal()

    try:

        cliente = (
            db.query(Cliente)
            .filter(
                Cliente.id
                == cliente_id
            )
            .first()
        )

        if not cliente:

            flash(
                "Cliente não encontrado."
            )

            return redirect(
                url_for("clientes")
            )

        if request.method == "POST":

            nome = request.form.get(
                "nome",
                ""
            ).strip()

            usuario = request.form.get(
                "usuario",
                ""
            ).strip()

            valor = valor_float(
                request.form.get(
                    "valor",
                    "0"
                )
            )

            vencimento_texto = (
                request.form.get(
                    "vencimento",
                    ""
                ).strip()
            )

            vencimento = (
                date.fromisoformat(
                    vencimento_texto
                )
                if vencimento_texto
                else None
            )

            outro = (
                db.query(Cliente)
                .filter(
                    Cliente.usuario == usuario,
                    Cliente.id != cliente_id
                )
                .first()
            )

            if outro:

                flash(
                    "Esse usuário já pertence a outro cliente."
                )

                return redirect(
                    url_for(
                        "editar_cliente",
                        cliente_id=cliente_id
                    )
                )

            cliente.nome = nome
            cliente.usuario = usuario
            cliente.valor = valor
            cliente.vencimento = vencimento

            db.commit()

            flash(
                "Cliente atualizado com sucesso."
            )

            return redirect(
                url_for("clientes")
            )

        return page(
            FORM,
            "Editar cliente",
            "clientes",
            c=cliente,
            editar=True
        )

    finally:

        db.close()


# ============================================================
# ALTERAR STATUS MANUAL
# ============================================================

@app.post(
    "/clientes/<int:cliente_id>/status"
)
@login_required
def alternar_status(
    cliente_id
):

    db = SessionLocal()

    try:

        cliente = (
            db.query(Cliente)
            .filter(
                Cliente.id == cliente_id
            )
            .first()
        )

        if not cliente:

            flash(
                "Cliente não encontrado."
            )

            return redirect(
                url_for("clientes")
            )

        if cliente.status == "Pago":

            cliente.status = "Pendente"
            cliente.data_pagamento = None

            flash(
                "Cliente alterado para pendente."
            )

        else:

            cliente.status = "Pago"

            cliente.data_pagamento = (
                datetime.utcnow()
            )

            cliente.vencimento = (
                calcular_proximo_vencimento(
                    cliente.vencimento
                )
            )

            flash(
                "Pagamento registrado e vencimento atualizado."
            )

        db.commit()

        return redirect(
            request.referrer
            or url_for("clientes")
        )

    except Exception:

        db.rollback()

        flash(
            "Não foi possível alterar o status."
        )

        return redirect(
            url_for("clientes")
        )

    finally:

        db.close()


# ============================================================
# EXCLUIR
# ============================================================

@app.post(
    "/clientes/<int:cliente_id>/excluir"
)
@login_required
def excluir_cliente(
    cliente_id
):

    db = SessionLocal()

    try:

        cliente = (
            db.query(Cliente)
            .filter(
                Cliente.id == cliente_id
            )
            .first()
        )

        if cliente:

            db.delete(cliente)
            db.commit()

            flash(
                "Cliente excluído com sucesso."
            )

        return redirect(
            url_for("clientes")
        )

    except Exception:

        db.rollback()

        flash(
            "Não foi possível excluir o cliente."
        )

        return redirect(
            url_for("clientes")
        )

    finally:

        db.close()


# ============================================================
# IMPORTAÇÃO CSV
# ============================================================

@app.route(
    "/importar",
    methods=["GET", "POST"]
)
@login_required
def importar():

    if request.method == "POST":

        arquivo = request.files.get(
            "arquivo"
        )

        if not arquivo or not arquivo.filename:

            flash(
                "Selecione um arquivo CSV."
            )

            return redirect(
                url_for("importar")
            )

        db = SessionLocal()

        adicionados = 0
        ignorados = 0

        try:

            import pandas as pd

            df = pd.read_csv(
                arquivo,
                sep=None,
                engine="python",
                dtype=str
            )

            colunas = {
                str(c).strip().lower(): c
                for c in df.columns
            }

            if (
                "nome" not in colunas
                or "usuario" not in colunas
            ):

                flash(
                    "O CSV precisa ter Nome e Usuario."
                )

                return redirect(
                    url_for("importar")
                )

            for _, row in df.iterrows():

                nome = str(
                    row[
                        colunas["nome"]
                    ]
                ).strip()

                usuario = str(
                    row[
                        colunas["usuario"]
                    ]
                ).strip()

                if (
                    not nome
                    or not usuario
                    or nome.lower() == "nan"
                    or usuario.lower() == "nan"
                ):

                    ignorados += 1
                    continue

                existente = (
                    db.query(Cliente)
                    .filter(
                        Cliente.usuario
                        == usuario
                    )
                    .first()
                )

                if existente:

                    ignorados += 1
                    continue

                valor = 0.0

                if "valor" in colunas:

                    try:

                        bruto = str(
                            row[
                                colunas["valor"]
                            ]
                        ).strip()

                        bruto = (
                            bruto
                            .replace("R$", "")
                            .replace(" ", "")
                        )

                        if (
                            "," in bruto
                            and "." in bruto
                        ):

                            bruto = (
                                bruto
                                .replace(".", "")
                                .replace(",", ".")
                            )

                        elif "," in bruto:

                            bruto = bruto.replace(
                                ",",
                                "."
                            )

                        valor = float(
                            bruto
                        )

                    except Exception:

                        valor = 0.0

                vencimento = None

                if "vencimento" in colunas:

                    try:

                        bruto = row[
                            colunas["vencimento"]
                        ]

                        if (
                            bruto
                            and str(
                                bruto
                            ).lower()
                            != "nan"
                        ):

                            vencimento = (
                                pd.to_datetime(
                                    bruto,
                                    dayfirst=True
                                ).date()
                            )

                    except Exception:

                        vencimento = None

                db.add(
                    Cliente(
                        nome=nome,
                        usuario=usuario,
                        valor=valor,
                        vencimento=vencimento,
                        status="Pendente"
                    )
                )

                adicionados += 1

            db.commit()

            flash(
                f"Importação concluída: "
                f"{adicionados} adicionados e "
                f"{ignorados} ignorados."
            )

            return redirect(
                url_for("clientes")
            )

        except Exception:

            db.rollback()

            flash(
                "Não foi possível processar o CSV."
            )

            return redirect(
                url_for("importar")
            )

        finally:

            db.close()

    content = r"""

<div class="top">

<div>

<h1>
    Importar clientes
</h1>

<div class="subtitle">
    Cadastre vários clientes de uma única vez.
</div>

</div>

<a
    class="btn secondary"
    href="{{ url_for('clientes') }}"
>
    ← Clientes
</a>

</div>

<div class="grid2">

<div class="card">

<h3>
    📥 Arquivo CSV
</h3>

<div class="subtitle">
    Envie sua lista de clientes.
</div>

<div class="detail-box" style="margin:18px 0;">

<div class="detail-label">
    Colunas obrigatórias
</div>

<div class="detail-value">
    Nome, Usuario
</div>

<div class="detail-label" style="margin-top:12px;">
    Colunas opcionais
</div>

<div class="detail-value">
    Valor, Vencimento
</div>

</div>

<form
    method="post"
    enctype="multipart/form-data"
>

<input
    type="file"
    name="arquivo"
    accept=".csv"
    required
>

<button
    class="btn primary full mt"
    type="submit"
>
    📥 Importar clientes
</button>

</form>

</div>

<div class="card">

<h3>
    📄 Exemplo
</h3>

<table style="margin-top:15px;">

<tr>
<th>Nome</th>
<th>Usuario</th>
<th>Valor</th>
<th>Vencimento</th>
</tr>

<tr>
<td>João Silva</td>
<td>joao123</td>
<td>25</td>
<td>10/10/2026</td>
</tr>

<tr>
<td>Maria Souza</td>
<td>maria456</td>
<td>40</td>
<td>15/10/2026</td>
</tr>

</table>

</div>

</div>

"""

    return page(
        content,
        "Importar clientes",
        "importar"
    )


# ============================================================
# PAGAMENTO PÚBLICO - BUSCAR CLIENTE
# ============================================================

@app.route(
    "/pagamento",
    methods=["GET", "POST"]
)
def pagamento():

    db = SessionLocal()

    try:

        busca = ""

        resultados = []

        if request.method == "POST":

            busca = request.form.get(
                "busca",
                ""
            ).strip()

        else:

            busca = request.args.get(
                "busca",
                ""
            ).strip()

        if len(busca) >= 2:

            resultados = (
                db.query(Cliente)
                .filter(
                    Cliente.nome.ilike(
                        f"%{busca}%"
                    )
                )
                .order_by(
                    Cliente.nome.asc()
                )
                .limit(20)
                .all()
            )

        content = r"""

<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
    Pagamento Pix
</title>

<style>

body {
    margin:0;
    min-height:100vh;
    background:
        linear-gradient(
            135deg,
            #060910,
            #0b1220
        );
    color:white;
    font-family:
        system-ui,
        sans-serif;
    padding:20px;
}

.box {
    width:min(600px,100%);
    margin:30px auto;
    background:#111827;
    border:1px solid #243044;
    border-radius:20px;
    padding:25px;
}

h1 {
    margin-top:0;
}

.sub {
    color:#94a3b8;
    font-size:13px;
    margin-bottom:20px;
}

input {
    width:100%;
    box-sizing:border-box;
    padding:14px;
    border-radius:10px;
    border:1px solid #243044;
    background:#0b1220;
    color:white;
    margin-bottom:10px;
}

button,
.btn {
    display:block;
    width:100%;
    padding:13px;
    border:0;
    border-radius:10px;
    background:#2563eb;
    color:white;
    font-weight:800;
    text-align:center;
    cursor:pointer;
}

.cliente {
    display:block;
    padding:15px;
    margin-top:10px;
    background:#0b1220;
    border:1px solid #243044;
    border-radius:12px;
}

.cliente:hover {
    border-color:#3b82f6;
}

.nome {
    font-weight:800;
}

.usuario {
    color:#94a3b8;
    font-size:12px;
    margin-top:3px;
}

</style>

</head>

<body>

<div class="box">

<h1>
    💳 Pagamento Pix
</h1>

<div class="sub">
    Digite seu primeiro nome para localizar seu cadastro.
</div>

<form method="post">

<input
    name="busca"
    value="{{ busca }}"
    minlength="2"
    required
    placeholder="Digite seu nome..."
    autofocus
>

<button>
    🔎 Procurar
</button>

</form>

{% if resultados %}

<div style="margin-top:20px;">

<strong>
    Selecione seu cadastro:
</strong>

{% for cliente in resultados %}

<a
    class="cliente"
    href="{{ url_for(
        'pagamento_valor',
        cliente_id=cliente.id
    ) }}"
>

<div class="nome">
    {{ cliente.nome }}
</div>

<div class="usuario">
    Usuário: {{ cliente.usuario }}
</div>

</a>

{% endfor %}

</div>

{% elif busca %}

<div
    style="
        margin-top:20px;
        color:#facc15;
    "
>
    Nenhum cliente encontrado.
</div>

{% endif %}

</div>

</body>

</html>

"""

        return render_template_string(
            content,
            busca=busca,
            resultados=resultados
        )

    finally:

        db.close()


# ============================================================
# PAGAMENTO - INFORMAR VALOR
# ============================================================

@app.route(
    "/pagamento/<int:cliente_id>",
    methods=["GET", "POST"]
)
def pagamento_valor(
    cliente_id
):

    db = SessionLocal()

    try:

        cliente = (
            db.query(Cliente)
            .filter(
                Cliente.id == cliente_id
            )
            .first()
        )

        if not cliente:

            return (
                "Cliente não encontrado.",
                404
            )

        if request.method == "POST":

            valor = valor_float(
                request.form.get(
                    "valor",
                    ""
                )
            )

            if valor is None:

                flash(
                    "Digite um valor válido."
                )

                return redirect(
                    url_for(
                        "pagamento_valor",
                        cliente_id=cliente_id
                    )
                )

            if valor <= 0:

                flash(
                    "O valor precisa ser maior que zero."
                )

                return redirect(
                    url_for(
                        "pagamento_valor",
                        cliente_id=cliente_id
                    )
                )

            referencia = (
                "IPTV-"
                + secrets.token_hex(16).upper()
            )

            pagamento = Pagamento(
                referencia=referencia,
                cliente_id=cliente.id,
                cliente_nome=cliente.nome,
                cliente_usuario=cliente.usuario,
                valor=valor,
                status="CONFIRMANDO"
            )

            db.add(pagamento)
            db.commit()

            return render_template_string(
                CONFIRMAR_PAGAMENTO,
                pagamento=pagamento
            )

        return render_template_string(
            PAGAMENTO_VALOR,
            cliente=cliente
        )

    finally:

        db.close()


# ============================================================
# TEMPLATE VALOR
# ============================================================

PAGAMENTO_VALOR = r"""

<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
    Valor do Pix
</title>

<style>

body {
    margin:0;
    min-height:100vh;
    background:
        linear-gradient(
            135deg,
            #060910,
            #0b1220
        );
    color:white;
    font-family:system-ui,sans-serif;
    padding:20px;
}

.box {
    width:min(500px,100%);
    margin:40px auto;
    background:#111827;
    border:1px solid #243044;
    border-radius:20px;
    padding:25px;
}

input {
    width:100%;
    box-sizing:border-box;
    padding:15px;
    background:#0b1220;
    color:white;
    border:1px solid #243044;
    border-radius:10px;
    font-size:18px;
}

button {
    width:100%;
    padding:14px;
    border:0;
    border-radius:10px;
    background:#2563eb;
    color:white;
    font-weight:800;
    margin-top:15px;
}

.muted {
    color:#94a3b8;
    font-size:13px;
}

</style>

</head>

<body>

<div class="box">

<h1>
    💳 Pagamento Pix
</h1>

<div class="muted">
    Cliente: <strong>{{ cliente.nome }}</strong>
</div>

<div class="muted" style="margin-top:5px;">
    Usuário: {{ cliente.usuario }}
</div>

<form method="post" style="margin-top:25px;">

<label>
    Quanto você vai pagar?
</label>

<input
    type="number"
    name="valor"
    step="0.01"
    min="0.01"
    required
    value="{{ cliente.valor or '' }}"
    placeholder="Ex.: 25,00"
>

<button>
    Continuar
</button>

</form>

</div>

</body>

</html>

"""


# ============================================================
# CONFIRMAÇÃO
# ============================================================

CONFIRMAR_PAGAMENTO = r"""

<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
    Confirmar pagamento
</title>

<style>

body {
    margin:0;
    min-height:100vh;
    background:
        linear-gradient(
            135deg,
            #060910,
            #0b1220
        );
    color:white;
    font-family:system-ui,sans-serif;
    padding:20px;
}

.box {
    width:min(500px,100%);
    margin:40px auto;
    background:#111827;
    border:1px solid #243044;
    border-radius:20px;
    padding:25px;
    text-align:center;
}

.valor {
    font-size:38px;
    font-weight:900;
    color:#60a5fa;
    margin:20px 0;
}

button {
    width:100%;
    padding:14px;
    border:0;
    border-radius:10px;
    color:white;
    font-weight:800;
    margin-top:10px;
}

.sim {
    background:#16a34a;
}

.nao {
    background:#374151;
}

</style>

</head>

<body>

<div class="box">

<h1>
    Confirmar pagamento
</h1>

<p>
    Cliente:
    <strong>
        {{ pagamento.cliente_nome }}
    </strong>
</p>

<p>
    Usuário:
    {{ pagamento.cliente_usuario }}
</p>

<div class="valor">
    R$ {{ "%.2f"|format(pagamento.valor)|replace(".", ",") }}
</div>

<p>
    Você confirma que deseja gerar este Pix?
</p>

<form
    method="post"
    action="{{ url_for(
        'criar_pagamento_pix',
        referencia=pagamento.referencia
    ) }}"
>

<button class="sim">
    ✅ SIM, GERAR PIX
</button>

</form>

<a
    href="{{ url_for('pagamento') }}"
    style="
        text-decoration:none;
        display:block;
    "
>

<button class="nao">
    ❌ NÃO
</button>

</a>

</div>

</body>

</html>

"""


# ============================================================
# CRIAR PIX APÓS SIM
# ============================================================

@app.post(
    "/pagamento/criar/<referencia>"
)
def criar_pagamento_pix(
    referencia
):

    db = SessionLocal()

    try:

        pagamento = (
            db.query(Pagamento)
            .filter(
                Pagamento.referencia
                == referencia
            )
            .first()
        )

        if not pagamento:

            return (
                "Pagamento não encontrado.",
                404
            )

        if pagamento.pagbank_order_id:

            return redirect(
                url_for(
                    "pix",
                    referencia=referencia
                )
            )

        try:

            criar_pix_pagbank(
                pagamento
            )

            db.commit()

        except Exception as error:

            db.rollback()

            return (
                "Não foi possível criar o Pix: "
                + str(error),
                500
            )

        return redirect(
            url_for(
                "pix",
                referencia=referencia
            )
        )

    finally:

        db.close()


# ============================================================
# PÁGINA PIX
# ============================================================

@app.route(
    "/pix/<referencia>"
)
def pix(
    referencia
):

    db = SessionLocal()

    try:

        pagamento = (
            db.query(Pagamento)
            .filter(
                Pagamento.referencia
                == referencia
            )
            .first()
        )

        if not pagamento:

            return (
                "Pagamento não encontrado.",
                404
            )

        content = r"""

<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
    Pagar com Pix
</title>

<style>

body {
    margin:0;
    min-height:100vh;
    background:
        linear-gradient(
            135deg,
            #060910,
            #0b1220
        );
    color:white;
    font-family:system-ui,sans-serif;
    padding:20px;
}

.box {
    width:min(550px,100%);
    margin:25px auto;
    background:#111827;
    border:1px solid #243044;
    border-radius:20px;
    padding:25px;
    text-align:center;
}

.qr {
    width:280px;
    max-width:100%;
    background:white;
    padding:10px;
    border-radius:15px;
}

.code {
    width:100%;
    min-height:110px;
    box-sizing:border-box;
    resize:vertical;
    background:#0b1220;
    border:1px solid #243044;
    color:white;
    border-radius:10px;
    padding:12px;
    font-size:11px;
}

button {
    width:100%;
    padding:13px;
    border:0;
    border-radius:10px;
    background:#2563eb;
    color:white;
    font-weight:800;
    margin-top:10px;
}

.wait {
    margin-top:20px;
    color:#93c5fd;
}

.success {
    padding:20px;
    border-radius:15px;
    background:rgba(34,197,94,.10);
    border:1px solid rgba(34,197,94,.30);
    color:#86efac;
}

</style>

</head>

<body>

<div class="box">

<h1>
    💳 Pagamento Pix
</h1>

<p>
    {{ pagamento.cliente_nome }}
</p>

<h2>
    R$ {{ "%.2f"|format(pagamento.valor)|replace(".", ",") }}
</h2>

<div id="status">

{% if pagamento.status == "PAID" %}

<div class="success">
    ✅ Pagamento confirmado!
</div>

{% else %}

<img
    class="qr"
    src="{{ url_for(
        'pix_qrcode',
        referencia=pagamento.referencia
    ) }}"
>

<h3>
    Escaneie o QR Code
</h3>

<textarea
    id="pix"
    class="code"
    readonly
>{{ pagamento.qr_code }}</textarea>

<button
    onclick="
        navigator.clipboard.writeText(
            document.getElementById('pix').value
        );
        this.innerText='✓ Copiado';
    "
>
    📋 Copiar Pix
</button>

<div class="wait">
    ⏳ Aguardando confirmação do pagamento...
</div>

{% endif %}

</div>

</div>

<script>

setInterval(async function(){

    try {

        const response =
            await fetch(
                "{{ url_for(
                    'status_pagamento',
                    referencia=pagamento.referencia
                ) }}"
            );

        const data =
            await response.json();

        if (
            data.status === "PAID"
        ) {

            location.reload();

        }

    } catch(error) {

    }

}, 5000);

</script>

</body>

</html>

"""

        return render_template_string(
            content,
            pagamento=pagamento
        )

    finally:

        db.close()


# ============================================================
# QR CODE PROXY
# ============================================================

@app.route(
    "/pix/<referencia>/qrcode.png"
)
def pix_qrcode(
    referencia
):

    db = SessionLocal()

    try:

        pagamento = (
            db.query(Pagamento)
            .filter(
                Pagamento.referencia
                == referencia
            )
            .first()
        )

        if not pagamento:

            return (
                "Não encontrado.",
                404
            )

        if not pagamento.qr_code_url:

            return (
                "QR Code não disponível.",
                404
            )

        resposta = requests.get(
            pagamento.qr_code_url,
            headers=pagbank_headers(),
            timeout=PAGBANK_TIMEOUT
        )

        if resposta.status_code != 200:

            return (
                "QR Code indisponível.",
                502
            )

        return Response(
            resposta.content,
            mimetype="image/png",
            headers={
                "Cache-Control":
                    "no-store"
            }
        )

    finally:

        db.close()


# ============================================================
# STATUS PÚBLICO
# ============================================================

@app.get(
    "/pagamento/status/<referencia>"
)
def status_pagamento(
    referencia
):

    db = SessionLocal()

    try:

        pagamento = (
            db.query(Pagamento)
            .filter(
                Pagamento.referencia
                == referencia
            )
            .first()
        )

        if not pagamento:

            return jsonify({
                "status": "NOT_FOUND"
            }), 404

        return jsonify({

            "status":
                pagamento.status,

            "pago":
                pagamento.status == "PAID"

        })

    finally:

        db.close()


# ============================================================
# PROCESSAR PAGAMENTO CONFIRMADO
# ============================================================

def processar_pagamento_confirmado(
    db,
    pagamento,
    dados_pedido
):

    charges = (
        dados_pedido.get(
            "charges"
        )
        or []
    )

    if not charges:

        pagamento.status = "ERRO"
        pagamento.observacao = (
            "Pedido sem cobrança."
        )

        return False

    charge = charges[0]

    status = (
        charge.get(
            "status"
        )
        or ""
    ).upper()

    amount = (
        charge.get(
            "amount"
        )
        or {}
    )

    valor_pago_centavos = (
        amount.get("value")
    )

    valor_esperado = (
        valor_centavos(
            pagamento.valor
        )
    )

    if status != "PAID":

        if status in (
            "DECLINED",
            "CANCELED",
            "CANCELLED"
        ):

            pagamento.status = "CANCELADO"

        return False

    try:

        valor_pago_centavos = int(
            valor_pago_centavos
        )

    except Exception:

        pagamento.status = "ERRO"

        pagamento.observacao = (
            "Valor retornado pelo PagBank inválido."
        )

        return False

    if (
        valor_pago_centavos
        != valor_esperado
    ):

        pagamento.status = "ERRO"

        pagamento.observacao = (
            "Valor pago diferente do valor da cobrança."
        )

        return False

    pagamento.status = "PAID"

    pagamento.pago_em = datetime.utcnow()

    pagamento.webhook_recebido_em = (
        datetime.utcnow()
    )

    pagamento.vencimento_apos_pagamento = (
        proximo_dia_10()
    )

    cliente = None

    if pagamento.cliente_id:

        cliente = (
            db.query(Cliente)
            .filter(
                Cliente.id
                == pagamento.cliente_id
            )
            .first()
        )

    if not cliente:

        if pagamento.cliente_usuario:

            cliente = (
                db.query(Cliente)
                .filter(
                    Cliente.usuario
                    == pagamento.cliente_usuario
                )
                .first()
            )

    if cliente:

        cliente.status = "Pago"

        cliente.valor = (
            pagamento.valor
        )

        cliente.data_pagamento = (
            datetime.utcnow()
        )

        cliente.vencimento = (
            proximo_dia_10()
        )

        pagamento.cliente_id = (
            cliente.id
        )

        pagamento.observacao = (
            "Pagamento confirmado e "
            "cliente atualizado automaticamente."
        )

    else:

        pagamento.status = (
            "PAID_UNLINKED"
        )

        pagamento.observacao = (
            "Pagamento recebido, mas "
            "cliente não foi localizado."
        )

    return True


# ============================================================
# WEBHOOK PAGBANK
# ============================================================

@app.post(
    "/webhooks/pagbank"
)
def webhook_pagbank():

    corpo = request.get_data(
        cache=False
    )

    assinatura = request.headers.get(
        "x-payload-signature"
    )

    if not assinatura:

        return jsonify({
            "error":
                "assinatura ausente"
        }), 401

    db = SessionLocal()

    try:

        if not PAGBANK_TOKEN:

            return jsonify({
                "error":
                    "PagBank não configurado"
            }), 500

        try:

            chave = (
                obter_chave_publica_pagbank(
                    db
                )
            )

        except Exception:

            db.rollback()

            return jsonify({
                "error":
                    "chave pública indisponível"
            }), 500

        autenticado = (
            validar_webhook_pagbank(
                corpo,
                assinatura,
                chave
            )
        )

        if not autenticado:

            return jsonify({
                "error":
                    "assinatura inválida"
            }), 401

        try:

            payload = (
                request.get_json(
                    force=True
                )
            )

        except Exception:

            return jsonify({
                "error":
                    "payload inválido"
            }), 400

        hash_evento = hashlib.sha256(
            corpo
        ).hexdigest()

        existente = (
            db.query(
                WebhookEvent
            )
            .filter(
                WebhookEvent.evento_hash
                == hash_evento
            )
            .first()
        )

        if existente:

            return (
                "",
                204
            )

        charges = (
            payload.get(
                "charges"
            )
            or []
        )

        order_id = (
            payload.get("id")
        )

        charge_id = None

        if charges:

            charge_id = (
                charges[0].get("id")
            )

        evento = WebhookEvent(
            evento_hash=hash_evento,
            order_id=order_id,
            charge_id=charge_id,
            sucesso=False
        )

        db.add(evento)

        db.flush()

        pagamento = None

        referencia = (
            payload.get(
                "reference_id"
            )
        )

        if referencia:

            pagamento = (
                db.query(Pagamento)
                .filter(
                    Pagamento.referencia
                    == referencia
                )
                .first()
            )

        if not pagamento and order_id:

            pagamento = (
                db.query(Pagamento)
                .filter(
                    Pagamento.pagbank_order_id
                    == order_id
                )
                .first()
            )

        if not pagamento and charge_id:

            pagamento = (
                db.query(Pagamento)
                .filter(
                    Pagamento.pagbank_charge_id
                    == charge_id
                )
                .first()
            )

        if not pagamento:

            evento.mensagem = (
                "Pagamento não vinculado."
            )

            evento.processado_em = (
                datetime.utcnow()
            )

            db.commit()

            return (
                "",
                204
            )

        if pagamento.status in (
            "PAID",
            "PAID_UNLINKED"
        ):

            evento.sucesso = True

            evento.mensagem = (
                "Evento já processado."
            )

            evento.processado_em = (
                datetime.utcnow()
            )

            db.commit()

            return (
                "",
                204
            )

        # ----------------------------------------------------
        # Consulta o pedido diretamente no PagBank.
        # Não confiamos somente no payload do webhook.
        # ----------------------------------------------------

        if not pagamento.pagbank_order_id:

            pagamento.pagbank_order_id = (
                order_id
            )

        if not pagamento.pagbank_order_id:

            evento.mensagem = (
                "Pedido PagBank ausente."
            )

            evento.processado_em = (
                datetime.utcnow()
            )

            db.commit()

            return (
                "",
                204
            )

        try:

            dados_pedido = (
                consultar_pedido_pagbank(
                    pagamento.pagbank_order_id
                )
            )

        except Exception as error:

            evento.mensagem = (
                "Falha ao consultar pedido: "
                + str(error)
            )

            evento.processado_em = (
                datetime.utcnow()
            )

            db.commit()

            return (
                "",
                204
            )

        confirmado = (
            processar_pagamento_confirmado(
                db,
                pagamento,
                dados_pedido
            )
        )

        evento.sucesso = bool(
            confirmado
            or pagamento.status
            in (
                "PAID",
                "PAID_UNLINKED"
            )
        )

        evento.processado_em = (
            datetime.utcnow()
        )

        evento.mensagem = (
            pagamento.observacao
            or "Evento processado."
        )

        db.commit()

        return (
            "",
            204
        )

    except Exception as error:

        db.rollback()

        return jsonify({
            "error":
                "Erro interno"
        }), 500

    finally:

        db.close()


# ============================================================
# PAGAMENTOS ADMIN
# ============================================================

@app.route(
    "/admin/pagamentos"
)
@login_required
def pagamentos_admin():

    db = SessionLocal()

    try:

        pagamentos = (
            db.query(Pagamento)
            .order_by(
                Pagamento.criado_em.desc()
            )
            .limit(200)
            .all()
        )

        content = r"""

<div class="top">

<div>

<h1>
    💳 Pagamentos Pix
</h1>

<div class="subtitle">
    Cobranças e confirmações automáticas do PagBank.
</div>

</div>

<a
    class="btn secondary"
    href="{{ url_for('relatorios') }}"
>
    📈 Relatórios
</a>

</div>

<div class="card">

<table>

<thead>

<tr>

<th>
    Data
</th>

<th>
    Cliente
</th>

<th>
    Valor
</th>

<th>
    Status
</th>

<th>
    PagBank
</th>

</tr>

</thead>

<tbody>

{% for p in pagamentos %}

<tr>

<td>
    {{ formatar_data(p.criado_em) }}
</td>

<td>

<strong>
    {{ p.cliente_nome or 'Não vinculado' }}
</strong>

<div
    style="
        color:#94a3b8;
        font-size:11px;
    "
>
    {{ p.cliente_usuario or '-' }}
</div>

</td>

<td>
    {{ dinheiro(p.valor) }}
</td>

<td>

<span
    class="badge
    {{
        'paid'
        if p.status in ['PAID','PAID_UNLINKED']
        else
        'waiting'
        if p.status in ['AGUARDANDO','CONFIRMANDO']
        else
        'pending'
    }}"
>

{{ p.status }}

</span>

</td>

<td>

<span
    style="
        font-size:11px;
        color:#94a3b8;
    "
>
    {{ p.pagbank_order_id or '-' }}
</span>

</td>

</tr>

{% else %}

<tr>

<td
    colspan="5"
    class="empty"
>
    Nenhum pagamento registrado.
</td>

</tr>

{% endfor %}

</tbody>

</table>

</div>

"""

        return page(
            content,
            "Pagamentos",
            "pagamentos",
            pagamentos=pagamentos
        )

    finally:

        db.close()


# ============================================================
# RELATÓRIO SEMANAL
# ============================================================

@app.route(
    "/admin/relatorios"
)
@login_required
def relatorios():

    db = SessionLocal()

    try:

        hoje = date.today()

        inicio = (
            hoje
            - timedelta(
                days=hoje.weekday()
            )
        )

        fim = (
            inicio
            + timedelta(days=7)
        )

        pagamentos = (
            db.query(Pagamento)
            .filter(
                Pagamento.status.in_(
                    [
                        "PAID",
                        "PAID_UNLINKED"
                    ]
                ),
                Pagamento.pago_em >=
                    datetime.combine(
                        inicio,
                        datetime.min.time()
                    ),
                Pagamento.pago_em <
                    datetime.combine(
                        fim,
                        datetime.min.time()
                    )
            )
            .order_by(
                Pagamento.pago_em.desc()
            )
            .all()
        )

        total = sum(
            float(p.valor or 0)
            for p in pagamentos
        )

        vinculados = sum(
            1
            for p in pagamentos
            if p.status == "PAID"
        )

        nao_vinculados = sum(
            1
            for p in pagamentos
            if p.status == "PAID_UNLINKED"
        )

        content = r"""

<div class="top">

<div>

<h1>
    📈 Relatório semanal
</h1>

<div class="subtitle">
    Semana de
    {{ inicio.strftime('%d/%m/%Y') }}
    até
    {{ (fim - timedelta(days=1)).strftime('%d/%m/%Y') }}
</div>

</div>

<a
    class="btn secondary"
    href="{{ url_for('pagamentos_admin') }}"
>
    💳 Pagamentos
</a>

</div>

<div class="grid">

<div class="card">

<div class="metric-label">
    💰 Total recebido
</div>

<div class="metric green">
    {{ dinheiro(total) }}
</div>

</div>

<div class="card">

<div class="metric-label">
    💳 Pagamentos
</div>

<div class="metric">
    {{ pagamentos|length }}
</div>

</div>

<div class="card">

<div class="metric-label">
    👤 Vinculados
</div>

<div class="metric green">
    {{ vinculados }}
</div>

</div>

<div class="card">

<div class="metric-label">
    ⚠️ Não vinculados
</div>

<div class="metric yellow">
    {{ nao_vinculados }}
</div>

</div>

</div>

<div class="card">

<h3>
    Pagamentos da semana
</h3>

<table>

<thead>

<tr>

<th>
    Data
</th>

<th>
    Cliente
</th>

<th>
    Usuário
</th>

<th>
    Valor
</th>

<th>
    Status
</th>

</tr>

</thead>

<tbody>

{% for p in pagamentos %}

<tr>

<td>
    {{ formatar_data(p.pago_em) }}
</td>

<td>
    {{ p.cliente_nome or 'Não localizado' }}
</td>

<td>
    {{ p.cliente_usuario or '-' }}
</td>

<td>
    {{ dinheiro(p.valor) }}
</td>

<td>
    {{ p.status }}
</td>

</tr>

{% else %}

<tr>

<td
    colspan="5"
    class="empty"
>
    Nenhum pagamento confirmado nesta semana.
</td>

</tr>

{% endfor %}

</tbody>

</table>

</div>

"""

        return page(
            content,
            "Relatórios",
            "relatorios",
            pagamentos=pagamentos,
            total=total,
            vinculados=vinculados,
            nao_vinculados=nao_vinculados,
            inicio=inicio,
            fim=fim,
            timedelta=timedelta
        )

    finally:

        db.close()


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    db = SessionLocal()

    try:

        db.execute(
            text("SELECT 1")
        )

        return {
            "status":
                "ok",

            "database":
                "connected",

            "pagbank":
                bool(PAGBANK_TOKEN),

            "environment":
                PAGBANK_ENV
        }

    except Exception as error:

        return {
            "status":
                "error",

            "database":
                str(error)
        }, 500

    finally:

        db.close()


# ============================================================
# 404
# ============================================================

@app.errorhandler(404)
def not_found(error):

    if session.get(
        "logged_in"
    ):

        return redirect(
            url_for("dashboard")
        )

    return redirect(
        url_for("login")
    )


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
