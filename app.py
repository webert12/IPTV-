import os
import csv
import io
from datetime import datetime, date
from decimal import Decimal
from functools import wraps

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    session,
    render_template_string,
    flash,
)
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Numeric,
    Date,
    DateTime,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import SQLAlchemyError


# ============================================================
# CONFIGURAÇÃO
# ============================================================

app = Flask(__name__)

SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    SECRET_KEY = "troque-esta-chave-no-render"

app.secret_key = SECRET_KEY

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DB_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL não configurada. "
        "Adicione o banco PostgreSQL nas Environment Variables do Render."
    )

# Compatibilidade com URLs antigas do Render
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql+psycopg://",
        1,
    )

elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://",
        "postgresql+psycopg://",
        1,
    )


# ============================================================
# BANCO DE DADOS
# ============================================================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=5,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)

Base = declarative_base()


class Cliente(Base):
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True)

    nome = Column(
        String(150),
        nullable=False,
    )

    usuario = Column(
        String(150),
        nullable=False,
        unique=True,
        index=True,
    )

    valor = Column(
        Numeric(10, 2),
        nullable=False,
        default=0,
    )

    vencimento = Column(
        Date,
        nullable=True,
    )

    status = Column(
        String(20),
        nullable=False,
        default="Pendente",
        index=True,
    )

    data_pagamento = Column(
        DateTime,
        nullable=True,
    )

    criado_em = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )


# Cria a tabela automaticamente
Base.metadata.create_all(bind=engine)


# ============================================================
# LOGIN ADMINISTRADOR
# ============================================================

ADMIN_USER = os.getenv(
    "ADMIN_USER",
    "admin",
)

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD",
    "admin123",
)


def login_required(function):
    @wraps(function)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return decorated_function


# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def dinheiro(valor):
    if valor is None:
        valor = 0

    try:
        valor = Decimal(str(valor))
    except Exception:
        valor = Decimal("0")

    texto = f"{valor:,.2f}"

    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_data(valor):
    if not valor:
        return "-"

    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y")

    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")

    return str(valor)


def parse_valor(valor):
    if valor is None:
        return Decimal("0.00")

    valor = str(valor).strip()

    if not valor:
        return Decimal("0.00")

    # Aceita:
    # 25
    # 25.50
    # 25,50
    # R$ 25,50
    valor = (
        valor.replace("R$", "")
        .replace(" ", "")
        .strip()
    )

    if "," in valor and "." in valor:
        valor = valor.replace(".", "")
        valor = valor.replace(",", ".")

    elif "," in valor:
        valor = valor.replace(",", ".")

    try:
        return Decimal(valor)
    except Exception:
        return Decimal("0.00")


def parse_data(valor):
    if not valor:
        return None

    valor = str(valor).strip()

    formatos = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ]

    for formato in formatos:
        try:
            return datetime.strptime(
                valor,
                formato,
            ).date()
        except ValueError:
            pass

    return None


def obter_cliente(db, cliente_id):
    return db.query(Cliente).filter(
        Cliente.id == cliente_id
    ).first()


# ============================================================
# TEMPLATE PRINCIPAL
# ============================================================

BASE_HTML = """
<!DOCTYPE html>
<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>{{ title }} - IPTV Manager</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    font-family:
        Inter,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background: #f4f6f9;
    color: #172033;
}

a {
    text-decoration: none;
}

.sidebar {
    position: fixed;
    left: 0;
    top: 0;
    bottom: 0;

    width: 250px;

    background:
        linear-gradient(
            180deg,
            #111827 0%,
            #172033 100%
        );

    color: white;

    padding: 25px 18px;

    z-index: 10;
}

.logo {
    font-size: 24px;
    font-weight: 800;
    margin-bottom: 5px;
}

.logo-sub {
    color: #94a3b8;
    font-size: 12px;
    margin-bottom: 35px;
}

.menu-title {
    color: #64748b;
    font-size: 11px;
    text-transform: uppercase;
    font-weight: 700;
    margin: 22px 10px 8px;
}

.menu a {
    display: block;
    color: #cbd5e1;
    padding: 12px 14px;
    margin: 4px 0;
    border-radius: 10px;
    font-size: 14px;
    transition: .2s;
}

.menu a:hover {
    background: rgba(255,255,255,.08);
    color: white;
}

.menu a.active {
    background: #2563eb;
    color: white;
    font-weight: 700;
}

.main {
    margin-left: 250px;
    min-height: 100vh;
}

.topbar {
    height: 72px;
    background: white;
    border-bottom: 1px solid #e5e7eb;

    display: flex;
    align-items: center;
    justify-content: space-between;

    padding: 0 30px;

    position: sticky;
    top: 0;
    z-index: 5;
}

.topbar-title {
    font-size: 20px;
    font-weight: 750;
}

.admin {
    color: #64748b;
    font-size: 13px;
}

.content {
    padding: 30px;
    max-width: 1500px;
    margin: auto;
}

.flash {
    padding: 13px 16px;
    border-radius: 10px;
    margin-bottom: 20px;
    background: #dbeafe;
    color: #1e40af;
    font-size: 14px;
}

.grid {
    display: grid;
    grid-template-columns:
        repeat(4, minmax(0, 1fr));
    gap: 18px;
}

.card {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 15px;
    padding: 22px;
    box-shadow:
        0 4px 18px rgba(15,23,42,.04);
}

.card-label {
    color: #64748b;
    font-size: 13px;
    margin-bottom: 8px;
}

.card-value {
    font-size: 26px;
    font-weight: 800;
}

.blue {
    color: #2563eb;
}

.green {
    color: #16a34a;
}

.yellow {
    color: #ca8a04;
}

.red {
    color: #dc2626;
}

.section {
    margin-top: 22px;
}

.section-title {
    font-size: 18px;
    font-weight: 750;
    margin-bottom: 15px;
}

.toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-bottom: 18px;
    flex-wrap: wrap;
}

.actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
}

.btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;

    border: 0;
    border-radius: 9px;

    padding: 10px 15px;

    cursor: pointer;

    font-size: 13px;
    font-weight: 700;

    transition: .2s;
}

.btn:hover {
    transform: translateY(-1px);
}

.btn-primary {
    background: #2563eb;
    color: white;
}

.btn-success {
    background: #16a34a;
    color: white;
}

.btn-warning {
    background: #eab308;
    color: #111827;
}

.btn-danger {
    background: #dc2626;
    color: white;
}

.btn-secondary {
    background: #e2e8f0;
    color: #334155;
}

.search {
    display: flex;
    gap: 8px;
    width: 100%;
    max-width: 550px;
}

.search input {
    flex: 1;
}

input,
select {
    width: 100%;
    padding: 12px 13px;

    border: 1px solid #dbe1e8;
    border-radius: 9px;

    background: white;
    color: #172033;

    font-size: 14px;

    outline: none;
}

input:focus,
select:focus {
    border-color: #2563eb;
    box-shadow:
        0 0 0 3px rgba(37,99,235,.10);
}

.form-grid {
    display: grid;
    grid-template-columns:
        repeat(2, minmax(0, 1fr));
    gap: 17px;
}

.form-group {
    margin-bottom: 5px;
}

.form-group label {
    display: block;
    font-size: 13px;
    font-weight: 700;
    margin-bottom: 7px;
    color: #475569;
}

.form-actions {
    margin-top: 22px;
    display: flex;
    gap: 9px;
}

.table-container {
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 15px;
    overflow-x: auto;
    box-shadow:
        0 4px 18px rgba(15,23,42,.04);
}

table {
    width: 100%;
    border-collapse: collapse;
    min-width: 800px;
}

th {
    background: #f8fafc;
    color: #64748b;
    font-size: 12px;
    text-transform: uppercase;
    text-align: left;
    padding: 14px 16px;
    border-bottom: 1px solid #e5e7eb;
}

td {
    padding: 14px 16px;
    border-bottom: 1px solid #eef2f7;
    font-size: 14px;
}

tr:last-child td {
    border-bottom: 0;
}

.customer-name {
    font-weight: 750;
}

.customer-user {
    color: #64748b;
    font-size: 12px;
    margin-top: 3px;
}

.status {
    display: inline-flex;
    padding: 5px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 800;
}

.status-paid {
    background: #dcfce7;
    color: #15803d;
}

.status-pending {
    background: #fef3c7;
    color: #a16207;
}

.row-actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
}

.small-btn {
    padding: 7px 9px;
    font-size: 11px;
}

.progress-box {
    margin-top: 15px;
}

.progress {
    height: 12px;
    width: 100%;
    background: #e2e8f0;
    border-radius: 20px;
    overflow: hidden;
}

.progress-bar {
    height: 100%;
    background: #16a34a;
    border-radius: 20px;
}

.chart {
    display: grid;
    gap: 15px;
}

.chart-item {
    display: grid;
    grid-template-columns: 100px 1fr 110px;
    align-items: center;
    gap: 12px;
}

.chart-label {
    font-size: 13px;
    font-weight: 700;
}

.chart-track {
    height: 20px;
    background: #e2e8f0;
    border-radius: 20px;
    overflow: hidden;
}

.chart-fill {
    height: 100%;
    border-radius: 20px;
}

.chart-green {
    background: #16a34a;
}

.chart-yellow {
    background: #eab308;
}

.chart-value {
    text-align: right;
    font-weight: 750;
    font-size: 13px;
}

.empty {
    padding: 45px;
    text-align: center;
    color: #64748b;
}

.login-page {
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;

    background:
        radial-gradient(
            circle at top left,
            #dbeafe,
            transparent 40%
        ),
        #f4f6f9;
}

.login-card {
    width: 100%;
    max-width: 420px;

    background: white;

    border-radius: 18px;

    padding: 35px;

    box-shadow:
        0 20px 60px rgba(15,23,42,.12);

    border: 1px solid #e5e7eb;
}

.login-logo {
    font-size: 28px;
    font-weight: 850;
    text-align: center;
}

.login-sub {
    text-align: center;
    color: #64748b;
    font-size: 13px;
    margin: 7px 0 30px;
}

.login-card .form-group {
    margin-bottom: 17px;
}

.login-card .btn {
    width: 100%;
    padding: 13px;
}

.footer {
    color: #94a3b8;
    text-align: center;
    font-size: 12px;
    padding: 30px 0;
}

@media (max-width: 1000px) {

    .grid {
        grid-template-columns:
            repeat(2, minmax(0, 1fr));
    }

}

@media (max-width: 700px) {

    .sidebar {
        position: relative;
        width: 100%;
        height: auto;
        padding: 18px;
    }

    .logo-sub {
        margin-bottom: 15px;
    }

    .menu-title {
        display: none;
    }

    .menu {
        display: grid;
        grid-template-columns:
            repeat(2, 1fr);
        gap: 5px;
    }

    .menu a {
        margin: 0;
        text-align: center;
        padding: 9px;
    }

    .main {
        margin-left: 0;
    }

    .topbar {
        height: 60px;
        padding: 0 17px;
    }

    .topbar-title {
        font-size: 16px;
    }

    .content {
        padding: 17px;
    }

    .grid {
        grid-template-columns: 1fr;
    }

    .form-grid {
        grid-template-columns: 1fr;
    }

    .chart-item {
        grid-template-columns:
            80px 1fr 90px;
    }

}

</style>

</head>

<body>

<div class="sidebar">

    <div class="logo">
        IPTV Manager
    </div>

    <div class="logo-sub">
        Gestão de clientes
    </div>

    <div class="menu">

        <div class="menu-title">
            Principal
        </div>

        <a
            href="{{ url_for('dashboard') }}"
            class="{{ 'active' if active == 'dashboard' else '' }}"
        >
            Dashboard
        </a>

        <a
            href="{{ url_for('clientes') }}"
            class="{{ 'active' if active == 'clientes' else '' }}"
        >
            Clientes
        </a>

        <div class="menu-title">
            Cadastro
        </div>

        <a
            href="{{ url_for('novo_cliente') }}"
            class="{{ 'active' if active == 'novo' else '' }}"
        >
            Novo cliente
        </a>

        <a
            href="{{ url_for('importar') }}"
            class="{{ 'active' if active == 'importar' else '' }}"
        >
            Importar clientes
        </a>

        <div class="menu-title">
            Sistema
        </div>

        <a href="{{ url_for('logout') }}">
            Sair
        </a>

    </div>

</div>


<div class="main">

    <div class="topbar">

        <div class="topbar-title">
            {{ title }}
        </div>

        <div class="admin">
            Administrador
        </div>

    </div>


    <main class="content">

        {% with messages = get_flashed_messages() %}

            {% if messages %}

                {% for message in messages %}

                    <div class="flash">
                        {{ message }}
                    </div>

                {% endfor %}

            {% endif %}

        {% endwith %}

        {{ content | safe }}

        <div class="footer">
            IPTV Manager • Sistema de gestão
        </div>

    </main>

</div>

</body>

</html>
"""


def page(template, title, active, **context):

    context["dinheiro"] = dinheiro
    context["formatar_data"] = formatar_data

    rendered = render_template_string(
        template,
        **context,
    )

    return render_template_string(
        BASE_HTML,
        content=rendered,
        title=title,
        active=active,
    )


# ============================================================
# LOGIN
# ============================================================

LOGIN_HTML = """
<div class="login-page">

    <div class="login-card">

        <div class="login-logo">
            IPTV Manager
        </div>

        <div class="login-sub">
            Acesso administrativo
        </div>

        <form method="POST">

            <div class="form-group">

                <label>
                    Usuário
                </label>

                <input
                    type="text"
                    name="username"
                    required
                    autocomplete="username"
                    placeholder="Digite seu usuário"
                >

            </div>

            <div class="form-group">

                <label>
                    Senha
                </label>

                <input
                    type="password"
                    name="password"
                    required
                    autocomplete="current-password"
                    placeholder="Digite sua senha"
                >

            </div>

            <button
                class="btn btn-primary"
                type="submit"
            >
                Entrar no sistema
            </button>

        </form>

    </div>

</div>
"""


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form.get(
            "username",
            "",
        ).strip()

        password = request.form.get(
            "password",
            "",
        )

        if (
            username == ADMIN_USER
            and password == ADMIN_PASSWORD
        ):

            session["logged_in"] = True

            return redirect(
                url_for("dashboard")
            )

        flash("Usuário ou senha incorretos.")

    return render_template_string(
        LOGIN_HTML
    )


@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/")
@app.route("/dashboard")
@login_required
def dashboard():

    db = SessionLocal()

    try:

        total = db.query(
            func.count(Cliente.id)
        ).scalar() or 0

        pagos = db.query(
            func.count(Cliente.id)
        ).filter(
            Cliente.status == "Pago"
        ).scalar() or 0

        pendentes = db.query(
            func.count(Cliente.id)
        ).filter(
            Cliente.status == "Pendente"
        ).scalar() or 0

        recebido = db.query(
            func.coalesce(
                func.sum(Cliente.valor),
                0,
            )
        ).filter(
            Cliente.status == "Pago"
        ).scalar() or 0

        previsto = db.query(
            func.coalesce(
                func.sum(Cliente.valor),
                0,
            )
        ).scalar() or 0

        pendente_valor = db.query(
            func.coalesce(
                func.sum(Cliente.valor),
                0,
            )
        ).filter(
            Cliente.status == "Pendente"
        ).scalar() or 0

        if total:
            percentual = (
                pagos / total
            ) * 100
        else:
            percentual = 0

        resumo = {
            "total": total,
            "pagos": pagos,
            "pendentes": pendentes,
            "recebido": recebido,
            "previsto": previsto,
            "pendente_valor": pendente_valor,
            "percentual": percentual,
        }

        content = """
        <div class="grid">

            <div class="card">
                <div class="card-label">
                    Total de clientes
                </div>

                <div class="card-value blue">
                    {{ resumo.total }}
                </div>
            </div>

            <div class="card">
                <div class="card-label">
                    Pagamentos recebidos
                </div>

                <div class="card-value green">
                    {{ resumo.pagos }}
                </div>
            </div>

            <div class="card">
                <div class="card-label">
                    Pendentes
                </div>

                <div class="card-value yellow">
                    {{ resumo.pendentes }}
                </div>
            </div>

            <div class="card">
                <div class="card-label">
                    Valor recebido
                </div>

                <div class="card-value green">
                    {{ dinheiro(resumo.recebido) }}
                </div>
            </div>

        </div>


        <div class="section">

            <div class="card">

                <div class="section-title">
                    Resumo financeiro
                </div>

                <div class="chart">

                    <div class="chart-item">

                        <div class="chart-label">
                            Recebido
                        </div>

                        <div class="chart-track">

                            {% if resumo.previsto > 0 %}

                                {% set recebido_pct =
                                    (resumo.recebido / resumo.previsto * 100)
                                %}

                            {% else %}

                                {% set recebido_pct = 0 %}

                            {% endif %}

                            <div
                                class="chart-fill chart-green"
                                style="width: {{ recebido_pct }}%;"
                            ></div>

                        </div>

                        <div class="chart-value">
                            {{ dinheiro(resumo.recebido) }}
                        </div>

                    </div>


                    <div class="chart-item">

                        <div class="chart-label">
                            Pendente
                        </div>

                        <div class="chart-track">

                            {% if resumo.previsto > 0 %}

                                {% set pendente_pct =
                                    (resumo.pendente_valor / resumo.previsto * 100)
                                %}

                            {% else %}

                                {% set pendente_pct = 0 %}

                            {% endif %}

                            <div
                                class="chart-fill chart-yellow"
                                style="width: {{ pendente_pct }}%;"
                            ></div>

                        </div>

                        <div class="chart-value">
                            {{ dinheiro(resumo.pendente_valor) }}
                        </div>

                    </div>

                </div>

            </div>

        </div>


        <div class="section">

            <div class="grid">

                <div class="card">

                    <div class="card-label">
                        Receita prevista
                    </div>

                    <div class="card-value blue">
                        {{ dinheiro(resumo.previsto) }}
                    </div>

                </div>

                <div class="card">

                    <div class="card-label">
                        Valor pendente
                    </div>

                    <div class="card-value yellow">
                        {{ dinheiro(resumo.pendente_valor) }}
                    </div>

                </div>

                <div class="card">

                    <div class="card-label">
                        Percentual pago
                    </div>

                    <div class="card-value green">
                        {{ "%.1f"|format(resumo.percentual) }}%
                    </div>

                </div>

                <div class="card">

                    <div class="card-label">
                        Percentual pendente
                    </div>

                    <div class="card-value red">
                        {{ "%.1f"|format(100 - resumo.percentual) }}%
                    </div>

                </div>

            </div>

        </div>
        """

        return page(
            content,
            "Dashboard",
            "dashboard",
            resumo=resumo,
        )

    except SQLAlchemyError as error:

        return (
            "Erro ao acessar o banco de dados: "
            + str(error),
            500,
        )

    finally:

        db.close()


# ============================================================
# CLIENTES
# ============================================================

@app.route("/clientes")
@login_required
def clientes():

    busca = request.args.get(
        "busca",
        "",
    ).strip()

    status = request.args.get(
        "status",
        "",
    ).strip()

    db = SessionLocal()

    try:

        query = db.query(
            Cliente
        )

        if busca:

            termo = f"%{busca}%"

            query = query.filter(
                (Cliente.nome.ilike(termo))
                |
                (Cliente.usuario.ilike(termo))
            )

        if status in ["Pago", "Pendente"]:

            query = query.filter(
                Cliente.status == status
            )

        lista = query.order_by(
            Cliente.nome.asc()
        ).all()

        content = """
        <div class="toolbar">

            <form
                method="GET"
                class="search"
            >

                <input
                    type="text"
                    name="busca"
                    value="{{ busca }}"
                    placeholder="Buscar por nome ou usuário..."
                >

                <select name="status">

                    <option value="">
                        Todos
                    </option>

                    <option
                        value="Pago"
                        {% if status == "Pago" %}
                            selected
                        {% endif %}
                    >
                        Pagos
                    </option>

                    <option
                        value="Pendente"
                        {% if status == "Pendente" %}
                            selected
                        {% endif %}
                    >
                        Pendentes
                    </option>

                </select>

                <button
                    type="submit"
                    class="btn btn-primary"
                >
                    Buscar
                </button>

            </form>


            <div class="actions">

                <a
                    href="{{ url_for('novo_cliente') }}"
                    class="btn btn-success"
                >
                    + Novo cliente
                </a>

                <a
                    href="{{ url_for('importar') }}"
                    class="btn btn-secondary"
                >
                    Importar CSV
                </a>

            </div>

        </div>


        <div class="table-container">

            {% if lista %}

            <table>

                <thead>

                    <tr>

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
                            Vencimento
                        </th>

                        <th>
                            Status
                        </th>

                        <th>
                            Pagamento
                        </th>

                        <th>
                            Ações
                        </th>

                    </tr>

                </thead>

                <tbody>

                    {% for c in lista %}

                    <tr>

                        <td>

                            <div class="customer-name">
                                {{ c.nome }}
                            </div>

                        </td>

                        <td>

                            <div class="customer-user">
                                {{ c.usuario }}
                            </div>

                        </td>

                        <td>
                            {{ dinheiro(c.valor) }}
                        </td>

                        <td>
                            {{ formatar_data(c.vencimento) }}
                        </td>

                        <td>

                            {% if c.status == "Pago" %}

                                <span class="status status-paid">
                                    PAGO
                                </span>

                            {% else %}

                                <span class="status status-pending">
                                    PENDENTE
                                </span>

                            {% endif %}

                        </td>

                        <td>
                            {{ formatar_data(c.data_pagamento) }}
                        </td>

                        <td>

                            <div class="row-actions">

                                <a
                                    href="{{ url_for(
                                        'editar_cliente',
                                        cliente_id=c.id
                                    ) }}"
                                    class="btn btn-primary small-btn"
                                >
                                    Editar
                                </a>


                                <form
                                    method="POST"
                                    action="{{ url_for(
                                        'alterar_status',
                                        cliente_id=c.id
                                    ) }}"
                                >

                                    {% if c.status == "Pago" %}

                                        <button
                                            class="btn btn-warning small-btn"
                                            type="submit"
                                        >
                                            Pendente
                                        </button>

                                    {% else %}

                                        <button
                                            class="btn btn-success small-btn"
                                            type="submit"
                                        >
                                            Marcar pago
                                        </button>

                                    {% endif %}

                                </form>


                                <form
                                    method="POST"
                                    action="{{ url_for(
                                        'excluir_cliente',
                                        cliente_id=c.id
                                    ) }}"
                                    onsubmit="return confirm(
                                        'Deseja realmente excluir este cliente?'
                                    );"
                                >

                                    <button
                                        class="btn btn-danger small-btn"
                                        type="submit"
                                    >
                                        Excluir
                                    </button>

                                </form>

                            </div>

                        </td>

                    </tr>

                    {% endfor %}

                </tbody>

            </table>

            {% else %}

                <div class="empty">

                    Nenhum cliente encontrado.

                </div>

            {% endif %}

        </div>
        """

        return page(
            content,
            "Clientes",
            "clientes",
            lista=lista,
            busca=busca,
            status=status,
        )

    except SQLAlchemyError as error:

        return (
            "Erro ao acessar o banco de dados: "
            + str(error),
            500,
        )

    finally:

        db.close()


# ============================================================
# NOVO CLIENTE
# ============================================================

@app.route(
    "/clientes/novo",
    methods=["GET", "POST"],
)
@login_required
def novo_cliente():

    if request.method == "POST":

        nome = request.form.get(
            "nome",
            "",
        ).strip()

        usuario = request.form.get(
            "usuario",
            "",
        ).strip()

        valor = parse_valor(
            request.form.get(
                "valor",
                "0",
            )
        )

        vencimento = parse_data(
            request.form.get(
                "vencimento",
                "",
            )
        )

        if not nome or not usuario:

            flash(
                "Nome e usuário são obrigatórios."
            )

            return redirect(
                url_for("novo_cliente")
            )

        db = SessionLocal()

        try:

            existente = db.query(
                Cliente
            ).filter(
                Cliente.usuario == usuario
            ).first()

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
                status="Pendente",
            )

            db.add(cliente)

            db.commit()

            flash(
                "Cliente cadastrado com sucesso."
            )

            return redirect(
                url_for("clientes")
            )

        except SQLAlchemyError as error:

            db.rollback()

            flash(
                "Erro ao cadastrar cliente: "
                + str(error)
            )

        finally:

            db.close()

    content = """
    <div class="card">

        <div class="section-title">
            Cadastrar novo cliente
        </div>

        <form method="POST">

            <div class="form-grid">

                <div class="form-group">

                    <label>
                        Nome do cliente
                    </label>

                    <input
                        type="text"
                        name="nome"
                        required
                        placeholder="Ex.: João Silva"
                    >

                </div>


                <div class="form-group">

                    <label>
                        Nome de usuário
                    </label>

                    <input
                        type="text"
                        name="usuario"
                        required
                        placeholder="Ex.: joao123"
                    >

                </div>


                <div class="form-group">

                    <label>
                        Valor mensal
                    </label>

                    <input
                        type="text"
                        name="valor"
                        placeholder="Ex.: 25,00"
                    >

                </div>


                <div class="form-group">

                    <label>
                        Vencimento
                    </label>

                    <input
                        type="date"
                        name="vencimento"
                    >

                </div>

            </div>


            <div class="form-actions">

                <button
                    type="submit"
                    class="btn btn-success"
                >
                    Salvar cliente
                </button>

                <a
                    href="{{ url_for('clientes') }}"
                    class="btn btn-secondary"
                >
                    Cancelar
                </a>

            </div>

        </form>

    </div>
    """

    return page(
        content,
        "Novo cliente",
        "novo",
    )


# ============================================================
# EDITAR CLIENTE
# ============================================================

@app.route(
    "/clientes/<int:cliente_id>/editar",
    methods=["GET", "POST"],
)
@login_required
def editar_cliente(cliente_id):

    db = SessionLocal()

    try:

        cliente = obter_cliente(
            db,
            cliente_id,
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
                "",
            ).strip()

            usuario = request.form.get(
                "usuario",
                "",
            ).strip()

            valor = parse_valor(
                request.form.get(
                    "valor",
                    "0",
                )
            )

            vencimento = parse_data(
                request.form.get(
                    "vencimento",
                    "",
                )
            )

            if not nome or not usuario:

                flash(
                    "Nome e usuário são obrigatórios."
                )

                return redirect(
                    url_for(
                        "editar_cliente",
                        cliente_id=cliente_id,
                    )
                )

            outro = db.query(
                Cliente
            ).filter(
                Cliente.usuario == usuario,
                Cliente.id != cliente_id,
            ).first()

            if outro:

                flash(
                    "Esse nome de usuário já pertence a outro cliente."
                )

                return redirect(
                    url_for(
                        "editar_cliente",
                        cliente_id=cliente_id,
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

        content = """
        <div class="card">

            <div class="section-title">
                Editar cliente
            </div>

            <form method="POST">

                <div class="form-grid">

                    <div class="form-group">

                        <label>
                            Nome do cliente
                        </label>

                        <input
                            type="text"
                            name="nome"
                            value="{{ cliente.nome }}"
                            required
                        >

                    </div>


                    <div class="form-group">

                        <label>
                            Nome de usuário
                        </label>

                        <input
                            type="text"
                            name="usuario"
                            value="{{ cliente.usuario }}"
                            required
                        >

                    </div>


                    <div class="form-group">

                        <label>
                            Valor mensal
                        </label>

                        <input
                            type="text"
                            name="valor"
                            value="{{ cliente.valor }}"
                        >

                    </div>


                    <div class="form-group">

                        <label>
                            Vencimento
                        </label>

                        <input
                            type="date"
                            name="vencimento"
                            value="{{
                                cliente.vencimento.strftime('%Y-%m-%d')
                                if cliente.vencimento
                                else ''
                            }}"
                        >

                    </div>

                </div>


                <div class="form-actions">

                    <button
                        type="submit"
                        class="btn btn-primary"
                    >
                        Salvar alterações
                    </button>

                    <a
                        href="{{ url_for('clientes') }}"
                        class="btn btn-secondary"
                    >
                        Cancelar
                    </a>

                </div>

            </form>

        </div>
        """

        return page(
            content,
            "Editar cliente",
            "clientes",
            cliente=cliente,
        )

    except SQLAlchemyError as error:

        db.rollback()

        return (
            "Erro ao editar cliente: "
            + str(error),
            500,
        )

    finally:

        db.close()


# ============================================================
# ALTERAR STATUS
# ============================================================

@app.route(
    "/clientes/<int:cliente_id>/status",
    methods=["POST"],
)
@login_required
def alterar_status(cliente_id):

    db = SessionLocal()

    try:

        cliente = obter_cliente(
            db,
            cliente_id,
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
            cliente.data_pagamento = datetime.utcnow()

            flash(
                "Pagamento registrado com sucesso."
            )

        db.commit()

    except SQLAlchemyError as error:

        db.rollback()

        flash(
            "Erro ao alterar status: "
            + str(error)
        )

    finally:

        db.close()

    return redirect(
        url_for("clientes")
    )


# ============================================================
# EXCLUIR CLIENTE
# ============================================================

@app.route(
    "/clientes/<int:cliente_id>/excluir",
    methods=["POST"],
)
@login_required
def excluir_cliente(cliente_id):

    db = SessionLocal()

    try:

        cliente = obter_cliente(
            db,
            cliente_id,
        )

        if cliente:

            db.delete(cliente)

            db.commit()

            flash(
                "Cliente excluído com sucesso."
            )

        else:

            flash(
                "Cliente não encontrado."
            )

    except SQLAlchemyError as error:

        db.rollback()

        flash(
            "Erro ao excluir cliente: "
            + str(error)
        )

    finally:

        db.close()

    return redirect(
        url_for("clientes")
    )


# ============================================================
# IMPORTAÇÃO CSV
# ============================================================

@app.route(
    "/importar",
    methods=["GET", "POST"],
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

        try:

            conteudo = arquivo.read().decode(
                "utf-8-sig"
            )

        except UnicodeDecodeError:

            try:

                arquivo.seek(0)

                conteudo = arquivo.read().decode(
                    "latin-1"
                )

            except Exception:

                flash(
                    "Não foi possível ler o arquivo."
                )

                return redirect(
                    url_for("importar")
                )

        try:

            amostra = conteudo[:4096]

            try:

                dialect = csv.Sniffer().sniff(
                    amostra,
                    delimiters=",;|",
                )

                delimitador = dialect.delimiter

            except csv.Error:

                delimitador = ","

            leitor = csv.DictReader(
                io.StringIO(conteudo),
                delimiter=delimitador,
            )

            if not leitor.fieldnames:

                flash(
                    "O CSV não possui cabeçalho."
                )

                return redirect(
                    url_for("importar")
                )

            # Normaliza nomes das colunas
            campos = {}

            for campo in leitor.fieldnames:

                if campo:

                    campos[
                        campo.strip().lower()
                    ] = campo

            nome_campo = None
            usuario_campo = None
            valor_campo = None
            vencimento_campo = None

            for nome in [
                "nome",
                "cliente",
                "name",
            ]:

                if nome in campos:

                    nome_campo = campos[nome]
                    break

            for nome in [
                "usuario",
                "usuário",
                "username",
                "user",
                "login",
            ]:

                if nome in campos:

                    usuario_campo = campos[nome]
                    break

            for nome in [
                "valor",
                "preco",
                "preço",
                "mensalidade",
            ]:

                if nome in campos:

                    valor_campo = campos[nome]
                    break

            for nome in [
                "vencimento",
                "vencimento_data",
                "data_vencimento",
                "due_date",
            ]:

                if nome in campos:

                    vencimento_campo = campos[nome]
                    break

            if not nome_campo or not usuario_campo:

                flash(
                    "O CSV precisa possuir as colunas "
                    "'nome' e 'usuario'."
                )

                return redirect(
                    url_for("importar")
                )

            db = SessionLocal()

            adicionados = 0
            atualizados = 0
            ignorados = 0

            try:

                for linha in leitor:

                    nome = str(
                        linha.get(
                            nome_campo,
                            "",
                        )
                    ).strip()

                    usuario = str(
                        linha.get(
                            usuario_campo,
                            "",
                        )
                    ).strip()

                    if not nome or not usuario:

                        ignorados += 1
                        continue

                    valor = Decimal("0.00")

                    if valor_campo:

                        valor = parse_valor(
                            linha.get(
                                valor_campo,
                                "",
                            )
                        )

                    vencimento = None

                    if vencimento_campo:

                        vencimento = parse_data(
                            linha.get(
                                vencimento_campo,
                                "",
                            )
                        )

                    existente = db.query(
                        Cliente
                    ).filter(
                        Cliente.usuario == usuario
                    ).first()

                    if existente:

                        existente.nome = nome

                        if valor_campo:
                            existente.valor = valor

                        if vencimento_campo:
                            existente.vencimento = vencimento

                        atualizados += 1

                    else:

                        cliente = Cliente(
                            nome=nome,
                            usuario=usuario,
                            valor=valor,
                            vencimento=vencimento,
                            status="Pendente",
                        )

                        db.add(cliente)

                        adicionados += 1

                db.commit()

                flash(
                    f"Importação concluída. "
                    f"Adicionados: {adicionados} | "
                    f"Atualizados: {atualizados} | "
                    f"Ignorados: {ignorados}"
                )

                return redirect(
                    url_for("clientes")
                )

            except SQLAlchemyError as error:

                db.rollback()

                flash(
                    "Erro no banco de dados: "
                    + str(error)
                )

            finally:

                db.close()

        except Exception as error:

            flash(
                "Erro ao processar CSV: "
                + str(error)
            )

    content = """
    <div class="card">

        <div class="section-title">
            Importar clientes
        </div>

        <p style="color:#64748b;font-size:14px;">
            Envie um arquivo CSV para cadastrar vários clientes
            de uma só vez.
        </p>

        <div
            style="
                background:#f8fafc;
                padding:18px;
                border-radius:12px;
                margin:20px 0;
            "
        >

            <strong>
                Formato recomendado:
            </strong>

            <br><br>

            <code>
                nome,usuario,valor,vencimento
            </code>

            <br><br>

            Exemplo:

            <br>

            <code>
                João Silva,joao123,25,10/10/2026
            </code>

        </div>


        <form
            method="POST"
            enctype="multipart/form-data"
        >

            <div class="form-group">

                <label>
                    Arquivo CSV
                </label>

                <input
                    type="file"
                    name="arquivo"
                    accept=".csv,text/csv"
                    required
                >

            </div>


            <div class="form-actions">

                <button
                    type="submit"
                    class="btn btn-primary"
                >
                    Importar clientes
                </button>

                <a
                    href="{{ url_for('clientes') }}"
                    class="btn btn-secondary"
                >
                    Cancelar
                </a>

            </div>

        </form>

    </div>
    """

    return page(
        content,
        "Importar clientes",
        "importar",
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    db = SessionLocal()

    try:

        db.execute(
            __import__("sqlalchemy").text(
                "SELECT 1"
            )
        )

        return {
            "status": "ok",
            "database": "connected",
        }

    except Exception as error:

        return {
            "status": "error",
            "database": str(error),
        }, 500

    finally:

        db.close()


# ============================================================
# ERRO 404
# ============================================================

@app.errorhandler(404)
def erro_404(error):

    if session.get("logged_in"):
        return redirect(
            url_for("dashboard")
        )

    return redirect(
        url_for("login")
    )


# ============================================================
# EXECUÇÃO LOCAL
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "5000",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
