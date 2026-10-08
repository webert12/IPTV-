import os
import json
import uuid
import base64
import hashlib
import secrets
import traceback
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

from flask import Flask, request, redirect, url_for, session, render_template_string, flash, jsonify
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Text, func, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
import requests

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-this-secret-key")

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DB_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não configurada no Render.")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=5,
)

Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

# PagBank / renovação pública — usa o MESMO aplicativo e banco do sistema.
APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "https://iptv-renovacao.onrender.com").strip().rstrip("/")
PAGBANK_TOKEN = os.getenv("PAGBANK_TOKEN", "").strip()
PAGBANK_ENV = os.getenv("PAGBANK_ENV", "production").strip().lower()
if PAGBANK_ENV not in ("production", "sandbox"):
    PAGBANK_ENV = "production"
PAGBANK_TIMEOUT = int(os.getenv("PAGBANK_TIMEOUT", "25"))
PAGBANK_BASE_URL = (
    "https://api.pagseguro.com"
    if PAGBANK_ENV == "production"
    else "https://sandbox.api.pagseguro.com"
)


class Cliente(Base):
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True)
    nome = Column(String(150), nullable=False)
    usuario = Column(String(150), nullable=False, unique=True, index=True)
    valor = Column(Float, nullable=False, default=0.0)
    vencimento = Column(Date, nullable=True)
    status = Column(String(20), nullable=False, default="Pendente", index=True)
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
    customer_email = Column(String(254), nullable=True)
    customer_tax_id = Column(String(20), nullable=True)
    customer_phone = Column(String(30), nullable=True)


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


def garantir_schema():
    """Garante colunas usadas pelo Sistema IPTV em bancos já existentes.
    create_all() não adiciona colunas novas em tabelas antigas, por isso
    fazemos uma migração compatível com PostgreSQL sem apagar dados.
    """
    if engine.dialect.name != "postgresql":
        return

    tabelas = {
        "clientes": {
            "nome": "VARCHAR(150)",
            "usuario": "VARCHAR(150)",
            "valor": "DOUBLE PRECISION DEFAULT 0",
            "vencimento": "DATE",
            "status": "VARCHAR(20) DEFAULT 'Pendente'",
            "data_pagamento": "TIMESTAMP",
            "criado_em": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        },
        "pagamentos": {
            "referencia": "VARCHAR(80)",
            "cliente_id": "INTEGER",
            "nome_cliente": "VARCHAR(150)",
            "usuario_cliente": "VARCHAR(150)",
            "valor": "DOUBLE PRECISION DEFAULT 0",
            "valor_centavos": "INTEGER DEFAULT 0",
            "status": "VARCHAR(40) DEFAULT 'AGUARDANDO'",
            "metodo": "VARCHAR(20) DEFAULT 'PIX'",
            "pagbank_order_id": "VARCHAR(100)",
            "pagbank_charge_id": "VARCHAR(100)",
            "qr_code_text": "TEXT",
            "qr_code_url": "TEXT",
            "criado_em": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "pago_em": "TIMESTAMP",
            "vencimento_gerado": "DATE",
            "webhook_recebido_em": "TIMESTAMP",
            "observacao": "TEXT",
            "idempotency_key": "VARCHAR(100)",
            "customer_email": "VARCHAR(254)",
            "customer_tax_id": "VARCHAR(20)",
            "customer_phone": "VARCHAR(30)",
        },
        "pagbank_webhook_eventos": {
            "evento_id": "VARCHAR(180)",
            "order_id": "VARCHAR(100)",
            "charge_id": "VARCHAR(100)",
            "payload_hash": "VARCHAR(64)",
            "recebido_em": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "processado_em": "TIMESTAMP",
            "status": "VARCHAR(30) DEFAULT 'RECEBIDO'",
            "detalhe": "TEXT",
        },
        "pagbank_config": {
            "chave": "VARCHAR(100)",
            "valor": "TEXT",
            "atualizado_em": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        },
    }

    with engine.begin() as conn:
        insp = inspect(conn)
        for tabela, colunas in tabelas.items():
            if tabela not in insp.get_table_names():
                continue
            existentes = {c["name"] for c in inspect(conn).get_columns(tabela)}
            for coluna, tipo in colunas.items():
                if coluna not in existentes:
                    conn.execute(text(f'ALTER TABLE "{tabela}" ADD COLUMN "{coluna}" {tipo}'))


garantir_schema()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def dinheiro(valor):
    return f"R$ {float(valor or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def obter_resumo(db):
    total = db.query(func.count(Cliente.id)).scalar() or 0
    pagos = db.query(func.count(Cliente.id)).filter(Cliente.status == "Pago").scalar() or 0
    pendentes = db.query(func.count(Cliente.id)).filter(Cliente.status == "Pendente").scalar() or 0
    recebido = db.query(func.coalesce(func.sum(Cliente.valor), 0)).filter(Cliente.status == "Pago").scalar() or 0
    pendente_valor = db.query(func.coalesce(func.sum(Cliente.valor), 0)).filter(Cliente.status == "Pendente").scalar() or 0
    return {
        "total": total,
        "pagos": pagos,
        "pendentes": pendentes,
        "recebido": float(recebido),
        "pendente_valor": float(pendente_valor),
        "previsto": float(recebido) + float(pendente_valor),
    }


BASE = r"""
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} · Sistema IPTV</title>
<style>
:root{
  --bg:#0b0f17;--panel:#111827;--panel2:#151e2d;--border:#243044;
  --text:#f8fafc;--muted:#94a3b8;--blue:#3b82f6;--green:#22c55e;
  --yellow:#facc15;--red:#ef4444;--shadow:0 16px 40px rgba(0,0,0,.25)
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(135deg,#080c13,#0f172a);color:var(--text);font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
a{text-decoration:none;color:inherit}
.layout{min-height:100vh;display:flex}
.sidebar{width:250px;background:rgba(10,15,24,.94);border-right:1px solid var(--border);padding:24px 16px;position:fixed;inset:0 auto 0 0;z-index:10}
.brand{display:flex;gap:12px;align-items:center;padding:8px 10px 28px}
.brand-icon{width:42px;height:42px;border-radius:12px;background:linear-gradient(135deg,#2563eb,#06b6d4);display:grid;place-items:center;font-size:22px}
.brand strong{display:block;font-size:18px}.brand span{font-size:12px;color:var(--muted)}
.nav-title{font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:1px;padding:0 12px 8px}
.nav a{display:block;padding:12px;border-radius:10px;color:#cbd5e1;margin:4px 0}
.nav a:hover,.nav a.active{background:#172236;color:#fff}
.logout{position:absolute;left:16px;right:16px;bottom:20px}
.logout a{display:block;text-align:center;padding:11px;border:1px solid var(--border);border-radius:10px;color:#cbd5e1}
.main{margin-left:250px;width:calc(100% - 250px);padding:30px;max-width:1500px}
.top{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:28px}
h1{font-size:30px;margin:0 0 5px}.subtitle{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:18px}
.grid2{display:grid;grid-template-columns:repeat(2,1fr);gap:18px}
.card{background:rgba(17,24,39,.86);border:1px solid var(--border);border-radius:16px;padding:20px;box-shadow:var(--shadow)}
.metric-label{color:var(--muted);font-size:13px}.metric{font-size:27px;font-weight:800;margin-top:8px}
.green{color:var(--green)}.yellow{color:var(--yellow)}.red{color:var(--red)}.blue{color:#60a5fa}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}
input,select{width:100%;background:#0b1220;color:#fff;border:1px solid var(--border);border-radius:10px;padding:12px;font:inherit;outline:none}
input:focus,select:focus{border-color:#3b82f6}
.form-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
label{display:block;color:#cbd5e1;font-size:13px;margin-bottom:7px}
.btn{border:0;border-radius:10px;padding:11px 15px;font:inherit;font-weight:700;cursor:pointer;display:inline-block}
.primary{background:var(--blue);color:white}.secondary{background:#1e293b;color:white;border:1px solid var(--border)}
.success{background:#14532d;color:#bbf7d0}.danger{background:#451a1a;color:#fecaca}
.actions{display:flex;gap:7px;flex-wrap:wrap}
.client{display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;gap:15px;align-items:center;padding:16px;border:1px solid var(--border);border-radius:14px;background:#101827;margin-bottom:10px}
.name{font-size:17px;font-weight:800}.username{font-size:13px;color:var(--muted);margin-top:3px}
.badge{display:inline-block;padding:6px 9px;border-radius:999px;font-size:12px;font-weight:800}
.badge.paid{background:rgba(34,197,94,.13);color:#4ade80}.badge.pending{background:rgba(250,204,21,.13);color:#fde047}
.flash{padding:12px 15px;border-radius:10px;background:#172554;border:1px solid #1d4ed8;margin-bottom:18px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px;border-bottom:1px solid var(--border);font-size:14px}th{color:#94a3b8;font-weight:600}
.empty{text-align:center;padding:40px;color:var(--muted)}
.login-page{min-height:100vh;display:grid;place-items:center;padding:20px}
.login{width:min(420px,100%);background:#111827;border:1px solid var(--border);border-radius:20px;padding:30px;box-shadow:var(--shadow)}
.login-brand{text-align:center;margin-bottom:25px}.login-brand .brand-icon{margin:auto}
.login h1{margin-top:12px}
.mt{margin-top:16px}.full{width:100%}
.bar{height:12px;background:#1e293b;border-radius:99px;overflow:hidden;margin-top:12px}.bar span{display:block;height:100%;background:linear-gradient(90deg,#22c55e,#3b82f6)}
@media(max-width:1000px){.grid{grid-template-columns:repeat(2,1fr)}.client{grid-template-columns:1fr 1fr}.main{padding:20px}}
@media(max-width:700px){.sidebar{width:100%;height:auto;position:relative;border-right:0;border-bottom:1px solid var(--border)}.layout{display:block}.main{margin-left:0;width:100%;padding:16px}.logout{position:static;margin-top:20px}.grid,.grid2,.form-grid{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}.client{grid-template-columns:1fr}.nav{display:grid;grid-template-columns:1fr 1fr}.brand{padding-bottom:15px}}
</style>
</head>
<body>
<div class="layout">
<aside class="sidebar">
  <div class="brand">
    <div class="brand-icon">📺</div>
    <div><strong>Sistema IPTV</strong><span>Administração</span></div>
  </div>
  <div class="nav-title">Menu</div>
  <nav class="nav">
    <a class="{{ 'active' if active=='dashboard' else '' }}" href="{{ url_for('dashboard') }}">📊 Dashboard</a>
    <a class="{{ 'active' if active=='clientes' else '' }}" href="{{ url_for('clientes') }}">👥 Clientes</a>
    <a class="{{ 'active' if active=='novo' else '' }}" href="{{ url_for('novo_cliente') }}">➕ Adicionar</a>
    <a class="{{ 'active' if active=='importar' else '' }}" href="{{ url_for('importar') }}">📥 Importar</a>
    <a href="{{ url_for('pagar') }}" target="_blank">💳 Pagamento PIX</a>
  </nav>
  <div class="logout"><a href="{{ url_for('logout') }}">🚪 Sair</a></div>
</aside>
<main class="main">
{% with messages=get_flashed_messages() %}
{% for message in messages %}<div class="flash">{{ message }}</div>{% endfor %}
{% endwith %}
{{ content|safe }}
</main>
</div>
</body>
</html>
"""

LOGIN = r"""
<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login · Sistema IPTV</title><style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(135deg,#080c13,#0f172a);color:#fff;font-family:Inter,system-ui}
.login{width:min(410px,92%);background:#111827;border:1px solid #243044;border-radius:20px;padding:32px;box-shadow:0 20px 60px #0006}
.icon{width:58px;height:58px;margin:auto;border-radius:16px;display:grid;place-items:center;background:linear-gradient(135deg,#2563eb,#06b6d4);font-size:29px}
h1{text-align:center;margin:15px 0 5px}.sub{text-align:center;color:#94a3b8;margin-bottom:25px}
label{display:block;color:#cbd5e1;font-size:13px;margin:13px 0 7px}input{width:100%;padding:12px;border-radius:10px;border:1px solid #243044;background:#0b1220;color:#fff;font-size:15px}
button{width:100%;margin-top:18px;padding:12px;border:0;border-radius:10px;background:#3b82f6;color:#fff;font-weight:800;font-size:15px;cursor:pointer}
.error{background:#451a1a;color:#fecaca;padding:10px;border-radius:9px;margin-bottom:12px}
</style></head><body><div class="login"><div class="icon">📺</div><h1>Sistema IPTV</h1><div class="sub">Painel administrativo</div>
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="post"><label>Usuário</label><input name="usuario" autocomplete="username" required><label>Senha</label><input type="password" name="senha" autocomplete="current-password" required><button>Entrar</button></form>
</div></body></html>
"""


def page(content, title, active):
    return render_template_string(
        BASE,
        content=render_template_string(content),
        title=title,
        active=active,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "")
        if usuario == ADMIN_USER and senha == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        return render_template_string(LOGIN, error="Usuário ou senha incorretos.")
    return render_template_string(LOGIN, error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = SessionLocal()
    try:
        resumo = obter_resumo(db)
        content = r"""
<div class="top"><div><h1>Dashboard</h1><div class="subtitle">Visão geral da sua operação</div></div></div>
<div class="card" style="margin-bottom:18px">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:15px;flex-wrap:wrap">
    <div><h3 style="margin:0 0 6px">💳 Link de Pagamento PIX</h3><div class="subtitle" style="margin:0">Envie este link aos seus clientes para pagamento via Pix.</div></div>
    <div class="actions"><a class="btn primary" href="{{ url_for('pagar') }}" target="_blank">Abrir página de renovação</a></div>
  </div>
  <div class="copybox" style="margin-top:14px">{{ public_renewal_url }}</div>
</div>
<div class="grid">
<div class="card"><div class="metric-label">👥 Total de clientes</div><div class="metric">{{ resumo.total }}</div></div>
<div class="card"><div class="metric-label">🟢 Clientes pagos</div><div class="metric green">{{ resumo.pagos }}</div></div>
<div class="card"><div class="metric-label">🟡 Pendentes</div><div class="metric yellow">{{ resumo.pendentes }}</div></div>
<div class="card"><div class="metric-label">💰 Total recebido</div><div class="metric blue">{{ dinheiro(resumo.recebido) }}</div></div>
</div>
<div class="grid2">
<div class="card"><div class="metric-label">💵 Valor pendente</div><div class="metric">{{ dinheiro(resumo.pendente_valor) }}</div></div>
<div class="card"><div class="metric-label">📈 Faturamento previsto</div><div class="metric">{{ dinheiro(resumo.previsto) }}</div></div>
</div>
<div class="grid2" style="margin-top:18px">
<div class="card"><h3>Distribuição de clientes</h3>
{% set percentual = (resumo.pagos / resumo.total * 100) if resumo.total else 0 %}
<div style="font-size:30px;font-weight:800">{{ "%.0f"|format(percentual) }}%</div>
<div class="subtitle">dos clientes estão pagos</div>
<div class="bar"><span style="width:{{ percentual }}%"></span></div>
<div style="display:flex;justify-content:space-between;margin-top:15px;color:#94a3b8;font-size:13px"><span>Pagos: {{ resumo.pagos }}</span><span>Pendentes: {{ resumo.pendentes }}</span></div>
</div>
<div class="card"><h3>Resumo financeiro</h3>
<table><tr><td>Recebido</td><td class="green"><strong>{{ dinheiro(resumo.recebido) }}</strong></td></tr>
<tr><td>Pendente</td><td class="yellow"><strong>{{ dinheiro(resumo.pendente_valor) }}</strong></td></tr>
<tr><td>Total previsto</td><td class="blue"><strong>{{ dinheiro(resumo.previsto) }}</strong></td></tr></table>
</div></div>
"""
        return render_template_string(BASE, content=render_template_string(content, resumo=resumo, dinheiro=dinheiro, public_renewal_url=APP_PUBLIC_URL+"/pagar"), title="Dashboard", active="dashboard")
    finally:
        db.close()


@app.route("/clientes")
@login_required
def clientes():
    db = SessionLocal()
    try:
        busca = request.args.get("busca", "").strip()
        status = request.args.get("status", "Todos")

        query = db.query(Cliente)

        if busca:
            termo = f"%{busca}%"
            query = query.filter(
                (Cliente.nome.ilike(termo)) | (Cliente.usuario.ilike(termo))
            )

        if status in ("Pago", "Pendente"):
            query = query.filter(Cliente.status == status)

        lista = query.order_by(Cliente.nome.asc()).all()

        content = r"""
<div class="top"><div><h1>Clientes</h1><div class="subtitle">Gerencie clientes, pagamentos e usuários.</div></div><a class="btn primary" href="{{ url_for('novo_cliente') }}">＋ Novo cliente</a></div>
<div class="card" style="margin-bottom:18px"><form method="get" class="toolbar">
<div style="flex:2;min-width:220px"><label>Pesquisar</label><input name="busca" value="{{ busca }}" placeholder="Nome ou usuário"></div>
<div style="flex:1;min-width:160px"><label>Status</label><select name="status"><option {{ 'selected' if status=='Todos' }}>Todos</option><option {{ 'selected' if status=='Pago' }}>Pago</option><option {{ 'selected' if status=='Pendente' }}>Pendente</option></select></div>
<div style="align-self:end"><button class="btn primary">Filtrar</button></div></form></div>
<div class="subtitle" style="margin-bottom:12px">{{ lista|length }} cliente(s) encontrado(s)</div>
{% if lista %}
{% for c in lista %}
<div class="client">
<div><div class="name {{ 'green' if c.status=='Pago' else 'yellow' }}">{{ c.nome }}</div><div class="username">{{ c.usuario }}</div></div>
<div><div class="metric-label">Mensalidade</div><strong>{{ dinheiro(c.valor) }}</strong></div>
<div><div class="metric-label">Vencimento</div><strong>{{ c.vencimento.strftime('%d/%m/%Y') if c.vencimento else '-' }}</strong></div>
<div><span class="badge {{ 'paid' if c.status=='Pago' else 'pending' }}">{{ '✓ Pago' if c.status=='Pago' else '⏳ Pendente' }}</span></div>
<div class="actions">
<form method="post" action="{{ url_for('alternar_status', cliente_id=c.id) }}"><button class="btn {{ 'secondary' if c.status=='Pago' else 'success' }}">{{ 'Marcar pendente' if c.status=='Pago' else 'Marcar pago' }}</button></form>
<a class="btn secondary" href="{{ url_for('editar_cliente', cliente_id=c.id) }}">Editar</a>
<form method="post" action="{{ url_for('excluir_cliente', cliente_id=c.id) }}" onsubmit="return confirm('Excluir este cliente definitivamente?')"><button class="btn danger">Excluir</button></form>
</div>
</div>
{% endfor %}
{% else %}<div class="card empty">Nenhum cliente encontrado.</div>{% endif %}
"""
        return page(content, "Clientes", "clientes")
    finally:
        db.close()


FORM = r"""
<div class="top"><div><h1>{{ 'Editar cliente' if editar else 'Adicionar cliente' }}</h1><div class="subtitle">{{ 'Atualize os dados do cliente.' if editar else 'Cadastre um novo cliente.' }}</div></div></div>
<div class="card">
<form method="post">
<div class="form-grid">
<div><label>Nome do cliente</label><input name="nome" value="{{ c.nome if c else '' }}" required maxlength="150" placeholder="Ex: João Silva"></div>
<div><label>Nome de usuário</label><input name="usuario" value="{{ c.usuario if c else '' }}" required maxlength="150" placeholder="Ex: joao123"></div>
<div><label>Valor da mensalidade</label><input type="number" step="0.01" min="0" name="valor" value="{{ c.valor if c else '25.00' }}" required></div>
<div><label>Data de vencimento</label><input type="date" name="vencimento" value="{{ c.vencimento.isoformat() if c and c.vencimento else '' }}"></div>
</div>
<div class="actions mt"><button class="btn primary">{{ 'Salvar alterações' if editar else 'Cadastrar cliente' }}</button><a class="btn secondary" href="{{ url_for('clientes') }}">Cancelar</a></div>
</form></div>
"""


@app.route("/clientes/novo", methods=["GET", "POST"])
@login_required
def novo_cliente():
    if request.method == "POST":
        db = SessionLocal()
        try:
            nome = request.form.get("nome", "").strip()
            usuario = request.form.get("usuario", "").strip()
            valor = float(request.form.get("valor", "0") or 0)
            vencimento_texto = request.form.get("vencimento", "").strip()
            vencimento = date.fromisoformat(vencimento_texto) if vencimento_texto else None

            if not nome or not usuario:
                flash("Nome e usuário são obrigatórios.")
                return redirect(url_for("novo_cliente"))

            if db.query(Cliente).filter(Cliente.usuario == usuario).first():
                flash("Esse nome de usuário já está cadastrado.")
                return redirect(url_for("novo_cliente"))

            db.add(Cliente(
                nome=nome,
                usuario=usuario,
                valor=valor,
                vencimento=vencimento,
                status="Pendente",
            ))
            db.commit()
            flash("Cliente cadastrado com sucesso.")
            return redirect(url_for("clientes"))
        except (ValueError, SQLAlchemyError) as e:
            db.rollback()
            flash("Não foi possível cadastrar o cliente.")
            return redirect(url_for("novo_cliente"))
        finally:
            db.close()

    return page(FORM.replace("{{ 'Editar cliente' if editar else 'Adicionar cliente' }}", "Adicionar cliente").replace("{{ 'Atualize os dados do cliente.' if editar else 'Cadastre um novo cliente.' }}", "Cadastre um novo cliente."), "Adicionar cliente", "novo")


@app.route("/clientes/<int:cliente_id>/editar", methods=["GET", "POST"])
@login_required
def editar_cliente(cliente_id):
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        if not cliente:
            flash("Cliente não encontrado.")
            return redirect(url_for("clientes"))

        if request.method == "POST":
            nome = request.form.get("nome", "").strip()
            usuario = request.form.get("usuario", "").strip()
            valor = float(request.form.get("valor", "0") or 0)
            vencimento_texto = request.form.get("vencimento", "").strip()
            vencimento = date.fromisoformat(vencimento_texto) if vencimento_texto else None

            outro = db.query(Cliente).filter(
                Cliente.usuario == usuario,
                Cliente.id != cliente_id
            ).first()

            if not nome or not usuario:
                flash("Nome e usuário são obrigatórios.")
                return redirect(url_for("editar_cliente", cliente_id=cliente_id))

            if outro:
                flash("Esse nome de usuário já pertence a outro cliente.")
                return redirect(url_for("editar_cliente", cliente_id=cliente_id))

            cliente.nome = nome
            cliente.usuario = usuario
            cliente.valor = valor
            cliente.vencimento = vencimento
            db.commit()
            flash("Cliente atualizado com sucesso.")
            return redirect(url_for("clientes"))

        return page(
            FORM,
            "Editar cliente",
            "clientes",
        ) if not False else render_template_string(
            BASE,
            content=render_template_string(FORM, c=cliente, editar=True),
            title="Editar cliente",
            active="clientes",
        )
    except (ValueError, SQLAlchemyError):
        db.rollback()
        flash("Não foi possível atualizar o cliente.")
        return redirect(url_for("clientes"))
    finally:
        db.close()


@app.post("/clientes/<int:cliente_id>/status")
@login_required
def alternar_status(cliente_id):
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        if not cliente:
            flash("Cliente não encontrado.")
            return redirect(url_for("clientes"))

        if cliente.status == "Pago":
            cliente.status = "Pendente"
            cliente.data_pagamento = None
        else:
            cliente.status = "Pago"
            cliente.data_pagamento = datetime.utcnow()

        db.commit()
        flash("Status atualizado.")
        return redirect(request.referrer or url_for("clientes"))
    except SQLAlchemyError:
        db.rollback()
        flash("Não foi possível alterar o status.")
        return redirect(url_for("clientes"))
    finally:
        db.close()


@app.post("/clientes/<int:cliente_id>/excluir")
@login_required
def excluir_cliente(cliente_id):
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        if cliente:
            db.delete(cliente)
            db.commit()
            flash("Cliente excluído com sucesso.")
        else:
            flash("Cliente não encontrado.")
        return redirect(url_for("clientes"))
    except SQLAlchemyError:
        db.rollback()
        flash("Não foi possível excluir o cliente.")
        return redirect(url_for("clientes"))
    finally:
        db.close()


@app.route("/importar", methods=["GET", "POST"])
@login_required
def importar():
    if request.method == "POST":
        arquivo = request.files.get("arquivo")

        if not arquivo or not arquivo.filename:
            flash("Selecione um arquivo CSV.")
            return redirect(url_for("importar"))

        db = SessionLocal()
        adicionados = 0
        ignorados = 0

        try:
            df = __import__("pandas").read_csv(
                arquivo,
                sep=None,
                engine="python",
                dtype=str,
            )

            colunas = {str(c).strip().lower(): c for c in df.columns}

            if "nome" not in colunas or "usuario" not in colunas:
                flash("O CSV precisa ter as colunas Nome e Usuario.")
                return redirect(url_for("importar"))

            for _, row in df.iterrows():
                nome = str(row[colunas["nome"]]).strip()
                usuario = str(row[colunas["usuario"]]).strip()

                if not nome or not usuario or nome.lower() == "nan" or usuario.lower() == "nan":
                    ignorados += 1
                    continue

                if db.query(Cliente).filter(Cliente.usuario == usuario).first():
                    ignorados += 1
                    continue

                valor = 0.0
                if "valor" in colunas:
                    try:
                        bruto = str(row[colunas["valor"]]).strip()
                        if bruto.lower() != "nan" and bruto:
                            valor = float(
                                bruto.replace("R$", "")
                                .replace(" ", "")
                                .replace(".", "")
                                .replace(",", ".")
                            )
                    except (ValueError, TypeError):
                        valor = 0.0

                vencimento = None
                if "vencimento" in colunas:
                    try:
                        bruto = row[colunas["vencimento"]]
                        if bruto and str(bruto).lower() != "nan":
                            vencimento = __import__("pandas").to_datetime(
                                bruto, dayfirst=True
                            ).date()
                    except Exception:
                        vencimento = None

                db.add(Cliente(
                    nome=nome,
                    usuario=usuario,
                    valor=valor,
                    vencimento=vencimento,
                    status="Pendente",
                ))
                adicionados += 1

            db.commit()
            flash(f"Importação concluída: {adicionados} adicionados e {ignorados} ignorados.")
            return redirect(url_for("clientes"))
        except Exception:
            db.rollback()
            flash("Não foi possível processar o arquivo CSV.")
            return redirect(url_for("importar"))
        finally:
            db.close()

    content = r"""
<div class="top"><div><h1>Importar clientes</h1><div class="subtitle">Cadastre vários clientes de uma única vez.</div></div></div>
<div class="grid2">
<div class="card"><h3>Arquivo CSV</h3><p class="subtitle">Colunas obrigatórias: <strong>Nome</strong> e <strong>Usuario</strong>. Valor e Vencimento são opcionais.</p>
<form method="post" enctype="multipart/form-data"><input type="file" name="arquivo" accept=".csv" required><button class="btn primary full mt">📥 Importar clientes</button></form></div>
<div class="card"><h3>Exemplo</h3><table><tr><th>Nome</th><th>Usuario</th><th>Valor</th><th>Vencimento</th></tr><tr><td>João Silva</td><td>joao123</td><td>25</td><td>10/10/2026</td></tr><tr><td>Maria Souza</td><td>maria456</td><td>40</td><td>15/10/2026</td></tr></table></div>
</div>
"""
    return page(content, "Importar clientes", "importar")


@app.errorhandler(404)
def not_found(error):
    return redirect(url_for("dashboard" if session.get("logged_in") else "login"))



# ============================================================
# RENOVAÇÃO IPTV / PAGBANK
# ============================================================

# ============================================================
# HELPERS
# ============================================================

def agora_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)




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
    token = PAGBANK_TOKEN.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    headers = {
        "Authorization": f"Bearer {token}",
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

def normalizar_cpf(valor):
    return re.sub(r"\D", "", str(valor or ""))


def validar_cpf(valor):
    cpf = normalizar_cpf(valor)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    digito1 = (soma * 10) % 11
    if digito1 == 10:
        digito1 = 0
    if digito1 != int(cpf[9]):
        return False
    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    digito2 = (soma * 10) % 11
    if digito2 == 10:
        digito2 = 0
    return digito2 == int(cpf[10])


def normalizar_telefone(valor):
    return re.sub(r"\D", "", str(valor or ""))


def validar_telefone(valor):
    numero = normalizar_telefone(valor)
    return len(numero) in (10, 11) and numero[0] in "23456789" if len(numero) == 10 else len(numero) == 11 and numero[2] in "9"


def dados_telefone_pagbank(valor):
    numero = normalizar_telefone(valor)
    if len(numero) == 11:
        return {"country": "55", "area": numero[:2], "number": numero[2:], "type": "MOBILE"}
    return {"country": "55", "area": numero[:2], "number": numero[2:], "type": "MOBILE"}


def criar_pedido_pix(pagamento):
    """Cria o pedido PIX no PagBank usando a sessão da rota chamadora.

    Esta função NÃO abre outra sessão SQLAlchemy e NÃO faz commit.
    A rota que chamou a função é responsável por persistir o resultado.
    """
    if not PAGBANK_TOKEN:
        raise RuntimeError("PAGBANK_TOKEN não configurado no Render.")

    if not APP_PUBLIC_URL.startswith("https://"):
        raise RuntimeError("APP_PUBLIC_URL precisa usar HTTPS para receber o webhook.")

    nome_cliente = " ".join(str(pagamento.nome_cliente or "Cliente IPTV").split()).strip()
    if not nome_cliente:
        nome_cliente = "Cliente IPTV"
    if len(nome_cliente) > 120:
        nome_cliente = nome_cliente[:120].strip()

    cpf_cliente = normalizar_cpf(pagamento.customer_tax_id)
    telefone_cliente = normalizar_telefone(pagamento.customer_phone)

    if not validar_cpf(cpf_cliente):
        raise RuntimeError("Informe um CPF válido para gerar o PIX.")
    if not validar_telefone(telefone_cliente):
        raise RuntimeError("Informe um celular válido para gerar o PIX.")

    # O PagBank exige e-mail no objeto customer. Como o cliente não informa
    # e-mail, usamos um endereço técnico único, sem pedir esse dado na tela.
    email_tecnico = f"cliente.{pagamento.referencia.lower()}@iptv-renovacao.onrender.com"

    customer = {
        "name": nome_cliente,
        "email": email_tecnico,
        "tax_id": cpf_cliente,
        "phones": [dados_telefone_pagbank(telefone_cliente)],
    }

    expiracao = datetime.now(timezone.utc) + timedelta(minutes=30)

    payload = {
        "reference_id": pagamento.referencia,
        "customer": customer,
        "items": [
            {
                "reference_id": pagamento.referencia,
                "name": "Pagamento IPTV",
                "quantity": 1,
                "unit_amount": int(pagamento.valor_centavos),
            }
        ],
        "notification_urls": [public_url("/webhook/pagbank")],
        "charges": [
            {
                "reference_id": pagamento.referencia,
                "description": "Pagamento IPTV",
                "amount": {
                    "value": int(pagamento.valor_centavos),
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

    try:
        resposta = requests.post(
            f"{PAGBANK_BASE_URL}/orders",
            headers=pagbank_headers(pagamento.idempotency_key),
            json=payload,
            timeout=PAGBANK_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Não foi possível conectar ao PagBank: {exc}") from exc

    if resposta.status_code not in (200, 201):
        erro = json_response_error(resposta)
        try:
            detalhe = json.dumps(erro, ensure_ascii=False)
        except Exception:
            detalhe = str(erro)
        raise RuntimeError(
            f"PagBank recusou a criação do PIX (HTTP {resposta.status_code}). "
            f"Resposta: {detalhe[:1800]}"
        )

    try:
        dados = resposta.json()
    except ValueError as exc:
        raise RuntimeError("O PagBank respondeu sem um JSON válido.") from exc

    charges = dados.get("charges") or []
    if not charges:
        raise RuntimeError(
            "PagBank criou o pedido, mas não retornou a cobrança PIX em charges."
        )

    charge = charges[0] or {}
    qr_code = charge.get("qr_code") or {}
    links = charge.get("links") or []

    qr_code_text = qr_code.get("text")
    qr_code_url = None
    for link in links:
        if link.get("rel") == "QRCODE.PNG":
            qr_code_url = link.get("href")
            break

    if not qr_code_text:
        raise RuntimeError(
            "PagBank não retornou o PIX copia e cola em charges.qr_code.text. "
            f"Resposta: {json.dumps(dados, ensure_ascii=False)[:1800]}"
        )

    pagamento.pagbank_order_id = dados.get("id")
    pagamento.pagbank_charge_id = charge.get("id")
    pagamento.qr_code_text = qr_code_text
    pagamento.qr_code_url = qr_code_url
    pagamento.status = charge.get("status") or "WAITING"

    if not pagamento.pagbank_order_id or not pagamento.pagbank_charge_id:
        raise RuntimeError("PagBank não retornou os identificadores do pedido/cobrança.")

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


def obter_public_key_webhook():
    chave_env = os.getenv("PAGBANK_WEBHOOK_PUBLIC_KEY", "").strip()
    if chave_env:
        return chave_env

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


def verificar_assinatura_webhook(raw_body, header):
    if not header:
        return False

    public_key_b64 = obter_public_key_webhook()

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

    if cliente:
        cliente.status = "Pago"
        cliente.data_pagamento = agora_utc()
        cliente.vencimento = proximo_vencimento()

        pagamento.status = "PAGO"
        pagamento.pago_em = agora_utc()
        pagamento.vencimento_gerado = cliente.vencimento
        pagamento.observacao = "Pagamento confirmado automaticamente pelo PagBank."
        return True

    pagamento.status = "RECEBIDO_SEM_VINCULO"
    pagamento.pago_em = agora_utc()
    pagamento.observacao = (
        "Pagamento recebido pelo PagBank, mas o cliente não foi encontrado "
        "para atualização automática."
    )
    return False




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

@app.route("/pagar", methods=["GET"])
@app.route("/renovacao", methods=["GET"])
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
              <h1>Sistema IPTV</h1>
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
              <h1>Sistema IPTV</h1>
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

                <label>4. Celular do pagador</label>
                <input
                    type="text"
                    name="celular"
                    inputmode="tel"
                    maxlength="15"
                    placeholder="(00) 90000-0000"
                    required
                >

                <label>5. CPF do pagador</label>
                <input
                    type="text"
                    name="cpf"
                    inputmode="numeric"
                    maxlength="14"
                    placeholder="000.000.000-00"
                    required
                >

                <p class="small">Informe seu celular e CPF. O e-mail não é solicitado.</p>

                <button type="submit">
                  Gerar PIX
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
    """Cria o pagamento PIX com tratamento completo de erros.
    Nenhuma exceção de banco/PagBank deve resultar em uma tela 500 genérica.
    """
    if not validar_csrf(request.form.get("csrf")):
        return "Solicitação inválida. Atualize a página e tente novamente.", 400

    cliente_id = session.get("cliente_pagamento_id")
    if not cliente_id:
        return redirect(url_for("pagar"))

    try:
        centavos = parse_centavos(request.form.get("valor"))
    except ValueError as exc:
        return render_template_string(
            BASE_CSS + """
            <div class="page"><div class="box">
              <div class="brand"><div class="icon">⚠️</div><h1>Valor inválido</h1></div>
              <div class="card"><p class="err">{{ erro }}</p>
                <a class="btn btn-secondary" href="{{ url_for('selecionar_cliente', cliente_id=cliente_id) }}">Voltar</a>
              </div>
            </div></div>
            """,
            erro=str(exc), cliente_id=cliente_id,
        ), 400

    celular_cliente = normalizar_telefone(request.form.get("celular"))
    cpf_cliente = normalizar_cpf(request.form.get("cpf"))

    if not validar_telefone(celular_cliente):
        return render_template_string(
            BASE_CSS + """
            <div class="page"><div class="box">
              <div class="brand"><div class="icon">⚠️</div><h1>Celular inválido</h1></div>
              <div class="card"><p class="err">Informe um celular válido com DDD.</p>
                <a class="btn btn-secondary" href="{{ url_for('selecionar_cliente', cliente_id=cliente_id) }}">Voltar</a>
              </div>
            </div></div>
            """,
            cliente_id=cliente_id,
        ), 400

    if not validar_cpf(cpf_cliente):
        return render_template_string(
            BASE_CSS + """
            <div class="page"><div class="box">
              <div class="brand"><div class="icon">⚠️</div><h1>CPF inválido</h1></div>
              <div class="card"><p class="err">Informe um CPF válido para gerar o PIX.</p>
                <a class="btn btn-secondary" href="{{ url_for('selecionar_cliente', cliente_id=cliente_id) }}">Voltar</a>
              </div>
            </div></div>
            """,
            cliente_id=cliente_id,
        ), 400

    pagamento_id = None
    referencia = None

    # 1) Grava o pagamento localmente primeiro.
    db = SessionLocal()
    try:
        cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
        if not cliente:
            return redirect(url_for("pagar"))

        referencia = "IPTV-" + secrets.token_urlsafe(18)
        pagamento = Pagamento(
            referencia=referencia,
            cliente_id=cliente.id,
            nome_cliente=cliente.nome,
            usuario_cliente=cliente.usuario,
            valor=centavos / 100,
            valor_centavos=centavos,
            status="CRIANDO",
            metodo="PIX",
            idempotency_key=str(uuid.uuid4()),
            customer_email=None,
            customer_tax_id=cpf_cliente,
            customer_phone=celular_cliente,
            criado_em=agora_utc(),
        )
        db.add(pagamento)
        db.commit()
        pagamento_id = pagamento.id
    except Exception as exc:
        db.rollback()
        return render_template_string(
            BASE_CSS + """
            <div class="page"><div class="box">
              <div class="brand"><div class="icon">⚠️</div><h1>Não foi possível continuar</h1></div>
              <div class="card">
                <p class="err">Não foi possível registrar sua solicitação de pagamento.</p>
                <p class="small">Verifique as configurações do sistema e tente novamente.</p>
                <a class="btn btn-secondary" href="{{ url_for('pagar') }}">Voltar</a>
              </div>
            </div></div>
            """,
        ), 500
    finally:
        db.close()

    # 2) Cria a cobrança no PagBank.
    db = SessionLocal()
    try:
        pagamento = db.query(Pagamento).filter(Pagamento.id == pagamento_id).first()
        if not pagamento:
            raise RuntimeError("Pagamento local não encontrado após o cadastro.")

        try:
            criar_pedido_pix(pagamento)
            db.commit()
        except Exception as exc:
            db.rollback()
            try:
                db.query(Pagamento).filter(Pagamento.id == pagamento.id).update({
                    Pagamento.status: "ERRO_CRIACAO",
                    Pagamento.observacao: str(exc)[:2000],
                })
                db.commit()
            except Exception:
                db.rollback()
            print("ERRO AO GERAR PIX:", repr(exc))
            traceback.print_exc()

            return render_template_string(
                BASE_CSS + """
                <div class="page"><div class="box">
                  <div class="brand"><div class="icon">⚠️</div><h1>Não foi possível gerar o PIX</h1></div>
                  <div class="card">
                    <p class="err">A cobrança não foi criada pelo PagBank.</p>
                    <p class="small">{{ erro_pagbank }}</p>
                    <p class="small">Ambiente: {{ ambiente }}. Se aparecer <strong>403 / ACCESS_DENIED</strong>, a API de pedidos em produção precisa estar liberada para sua conta PagBank.</p>
                    <a class="btn btn-secondary" href="{{ url_for('pagar') }}">Voltar</a>
                  </div>
                </div></div>
                """,
                erro_pagbank=str(exc)[:1500],
                ambiente=PAGBANK_ENV,
            ), 502

        return redirect(url_for("pix", referencia=pagamento.referencia))
    except Exception as exc:
        db.rollback()
        print("ERRO INTERNO EM /confirmar-pagamento:", repr(exc))
        traceback.print_exc()
        return render_template_string(
            BASE_CSS + """
            <div class="page"><div class="box">
              <div class="brand"><div class="icon">⚠️</div><h1>Erro ao gerar o PIX</h1></div>
              <div class="card">
                <p class="err">O sistema encontrou um erro ao finalizar o pagamento.</p>
                <p class="small">{{ erro }}</p>
                <a class="btn btn-secondary" href="{{ url_for('pagar') }}">Voltar</a>
              </div>
            </div></div>
            """,
            erro=str(exc)[:1500],
        ), 500
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
                  <h1>Sistema IPTV</h1>
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
        pagamento = db.query(Pagamento).filter(Pagamento.referencia == referencia).first()
        if not pagamento:
            return jsonify({"status": "NAO_ENCONTRADO"}), 404

        # Fallback: consulta o PagBank diretamente caso o webhook ainda não tenha chegado.
        if pagamento.status not in ("PAGO", "CANCELADO", "ERRO_VALOR") and pagamento.pagbank_order_id:
            pedido = consultar_pedido(pagamento.pagbank_order_id)
            if pedido:
                dados = extrair_dados_pagbank(pedido)
                status = str(dados.get("status") or "").upper()
                if status == "PAID":
                    processar_pagamento_pago(db, pagamento, dados.get("valor"))
                    db.commit()
                elif status in ("CANCELED", "CANCELLED", "DECLINED"):
                    pagamento.status = "CANCELADO"
                    db.commit()

        vencimento = pagamento.vencimento_gerado.strftime("%d/%m/%Y") if pagamento.vencimento_gerado else ""
        return jsonify({"status": pagamento.status, "vencimento": vencimento})
    except Exception:
        db.rollback()
        vencimento = pagamento.vencimento_gerado.strftime("%d/%m/%Y") if pagamento.vencimento_gerado else ""
        return jsonify({"status": pagamento.status, "vencimento": vencimento})
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
        if not verificar_assinatura_webhook(raw_body, assinatura):
            return "Assinatura inválida.", 401
    except Exception:
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




def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in") and not session.get("admin_logado"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


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
# HEALTH PAGBANK / APLICAÇÃO
# ============================================================

@app.route("/health")
def health_pagbank():
    return jsonify({
        "ok": True,
        "servico": "Sistema IPTV",
        "pagamento_pix": APP_PUBLIC_URL + "/pagar",
        "pagbank_ambiente": PAGBANK_ENV,
        "pagbank_configurado": bool(PAGBANK_TOKEN),
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
