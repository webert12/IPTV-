import os
import calendar
from datetime import datetime, date
from functools import wraps
from collections import defaultdict

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
    Float,
    Date,
    DateTime,
    func,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import SQLAlchemyError


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


# ============================================================
# PRÓXIMO VENCIMENTO APÓS PAGAMENTO
# ============================================================

def calcular_proximo_vencimento(vencimento_atual):

    if not vencimento_atual:
        return None

    # Sempre será o dia 10 do mês seguinte
    ano = vencimento_atual.year
    mes = vencimento_atual.month

    if mes == 12:
        mes = 1
        ano += 1
    else:
        mes += 1

    # O dia será SEMPRE 10
    return date(
        ano,
        mes,
        10
    )


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

    recebido = float(recebido)
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
        "pendente_valor": pendente_valor,
        "previsto": previsto,
        "percentual": percentual,
    }


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
            "label": (
                f"{nomes_meses[mes - 1]}"
                f"/{str(ano)[-2:]}"
            ),
            "valor": round(
                valor,
                2
            )
        })

    maior = max(
        [x["valor"] for x in resultado]
        or [1]
    )

    for item in resultado:

        if maior > 0:
            item["percentual"] = (
                item["valor"]
                / maior
            ) * 100
        else:
            item["percentual"] = 0

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
# DESIGN PRINCIPAL
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

    --bg: #070b12;
    --bg2: #0b1220;

    --panel: #111827;
    --panel2: #151e2d;

    --border: #243044;

    --text: #f8fafc;
    --muted: #94a3b8;

    --blue: #3b82f6;
    --blue2: #2563eb;

    --green: #22c55e;
    --yellow: #facc15;
    --red: #ef4444;

    --cyan: #06b6d4;

    --shadow:
        0 18px 50px rgba(0,0,0,.25);
}


* {
    box-sizing: border-box;
}


html {
    scroll-behavior: smooth;
}


body {

    margin: 0;

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

    color: var(--text);

    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
}


a {
    text-decoration: none;
    color: inherit;
}


button,
input,
select {
    font: inherit;
}


.layout {

    min-height: 100vh;

    display: flex;
}


.sidebar {

    width: 255px;

    background:
        rgba(8,13,22,.94);

    border-right:
        1px solid var(--border);

    padding:
        22px 15px;

    position: fixed;

    inset:
        0 auto 0 0;

    z-index: 100;

    backdrop-filter:
        blur(18px);
}


.brand {

    display: flex;

    align-items: center;

    gap: 12px;

    padding:
        7px 10px 25px;
}


.brand-icon {

    width: 45px;
    height: 45px;

    border-radius: 13px;

    background:
        linear-gradient(
            135deg,
            #2563eb,
            #06b6d4
        );

    display: grid;

    place-items: center;

    font-size: 22px;

    box-shadow:
        0 8px 25px
        rgba(37,99,235,.25);
}


.brand strong {

    display: block;

    font-size: 18px;

    font-weight: 850;
}


.brand span {

    display: block;

    font-size: 11px;

    color: var(--muted);

    margin-top: 2px;
}


.nav-title {

    font-size: 10px;

    color: #64748b;

    text-transform: uppercase;

    letter-spacing: 1.3px;

    padding:
        0 12px 8px;
}


.nav a {

    display: flex;

    align-items: center;

    gap: 10px;

    padding: 12px;

    border-radius: 11px;

    color: #cbd5e1;

    margin: 4px 0;

    transition: .2s;
}


.nav a:hover {

    background:
        rgba(255,255,255,.05);

    color: white;

    transform:
        translateX(2px);
}


.nav a.active {

    background:
        linear-gradient(
            90deg,
            rgba(37,99,235,.25),
            rgba(37,99,235,.08)
        );

    color: white;

    border:
        1px solid
        rgba(59,130,246,.25);
}


.sidebar-footer {

    position: absolute;

    left: 15px;
    right: 15px;
    bottom: 20px;
}


.logout {

    display: block;

    text-align: center;

    padding: 11px;

    border:
        1px solid var(--border);

    border-radius: 11px;

    color: #cbd5e1;

    transition: .2s;
}


.logout:hover {

    background:
        rgba(239,68,68,.10);

    color: #fecaca;
}


.main {

    margin-left: 255px;

    width:
        calc(100% - 255px);

    min-height: 100vh;

    padding: 30px;

    max-width: 1600px;
}


.top {

    display: flex;

    justify-content:
        space-between;

    align-items: center;

    gap: 15px;

    margin-bottom: 25px;
}


h1 {

    font-size: 30px;

    margin:
        0 0 5px;

    font-weight: 850;
}


h2,
h3 {

    margin-top: 0;
}


.subtitle {

    color: var(--muted);

    font-size: 13px;
}


.grid {

    display: grid;

    grid-template-columns:
        repeat(4,1fr);

    gap: 16px;

    margin-bottom: 18px;
}


.grid2 {

    display: grid;

    grid-template-columns:
        repeat(2,1fr);

    gap: 18px;
}


.card {

    background:
        linear-gradient(
            145deg,
            rgba(17,24,39,.94),
            rgba(13,20,32,.94)
        );

    border:
        1px solid var(--border);

    border-radius: 17px;

    padding: 20px;

    box-shadow: var(--shadow);

    position: relative;

    overflow: hidden;
}


.card::after {

    content: "";

    position: absolute;

    width: 100px;
    height: 100px;

    right: -55px;
    top: -55px;

    border-radius: 50%;

    background:
        rgba(59,130,246,.05);
}


.metric-label {

    color: var(--muted);

    font-size: 12px;

    position: relative;

    z-index: 2;
}


.metric {

    font-size: 27px;

    font-weight: 850;

    margin-top: 8px;

    position: relative;

    z-index: 2;
}


.metric-small {

    color: var(--muted);

    font-size: 12px;

    margin-top: 5px;
}


.green {
    color: var(--green);
}


.yellow {
    color: var(--yellow);
}


.red {
    color: var(--red);
}


.blue {
    color: #60a5fa;
}


.cyan {
    color: #22d3ee;
}


.toolbar {

    display: flex;

    gap: 10px;

    flex-wrap: wrap;

    margin-bottom: 18px;
}


input,
select {

    width: 100%;

    background:
        #0a111e;

    color: white;

    border:
        1px solid var(--border);

    border-radius: 10px;

    padding: 12px;

    outline: none;
}


input:focus,
select:focus {

    border-color:
        var(--blue);

    box-shadow:
        0 0 0 3px
        rgba(59,130,246,.10);
}


.form-grid {

    display: grid;

    grid-template-columns:
        repeat(2,1fr);

    gap: 16px;
}


label {

    display: block;

    color: #cbd5e1;

    font-size: 13px;

    margin-bottom: 7px;

    font-weight: 650;
}


.btn {

    border: 0;

    border-radius: 10px;

    padding:
        10px 14px;

    font-weight: 750;

    cursor: pointer;

    display: inline-flex;

    align-items: center;

    justify-content: center;

    gap: 6px;

    transition: .2s;
}


.btn:hover {

    transform:
        translateY(-1px);

    filter:
        brightness(1.08);
}


.primary {

    background:
        linear-gradient(
            135deg,
            #3b82f6,
            #2563eb
        );

    color: white;
}


.secondary {

    background:
        #172236;

    color: white;

    border:
        1px solid var(--border);
}


.success {

    background:
        rgba(34,197,94,.14);

    color:
        #86efac;

    border:
        1px solid
        rgba(34,197,94,.20);
}


.danger {

    background:
        rgba(239,68,68,.12);

    color:
        #fecaca;

    border:
        1px solid
        rgba(239,68,68,.20);
}


.warning {

    background:
        rgba(250,204,21,.12);

    color:
        #fde68a;
}


.actions {

    display: flex;

    gap: 7px;

    flex-wrap: wrap;
}


.full {
    width: 100%;
}


.mt {
    margin-top: 16px;
}


.flash {

    padding:
        13px 16px;

    border-radius: 11px;

    background:
        rgba(37,99,235,.12);

    border:
        1px solid
        rgba(59,130,246,.25);

    margin-bottom: 18px;

    color: #bfdbfe;
}


.client {

    display: grid;

    grid-template-columns:
        2fr 1fr 1fr 1fr auto;

    gap: 15px;

    align-items: center;

    padding: 16px;

    border:
        1px solid var(--border);

    border-radius: 15px;

    background:
        rgba(16,24,39,.82);

    margin-bottom: 10px;

    transition: .2s;
}


.client:hover {

    border-color:
        rgba(59,130,246,.30);

    transform:
        translateY(-1px);
}


.name {

    font-size: 16px;

    font-weight: 850;
}


.username {

    font-size: 12px;

    color: var(--muted);

    margin-top: 3px;
}


.badge {

    display:
        inline-flex;

    align-items: center;

    padding:
        6px 10px;

    border-radius: 999px;

    font-size: 11px;

    font-weight: 850;
}


.badge.paid {

    background:
        rgba(34,197,94,.13);

    color:
        #4ade80;
}


.badge.pending {

    background:
        rgba(250,204,21,.13);

    color:
        #fde047;
}


.drawer {

    margin-top: 9px;

    border:
        1px solid var(--border);

    border-radius: 12px;

    overflow: hidden;

    background:
        rgba(8,13,22,.55);
}


.drawer summary {

    cursor: pointer;

    padding: 11px 13px;

    color: #cbd5e1;

    font-size: 12px;

    font-weight: 700;

    list-style: none;
}


.drawer summary::-webkit-details-marker {
    display: none;
}


.drawer-content {

    padding:
        14px;

    border-top:
        1px solid var(--border);

    display: grid;

    grid-template-columns:
        repeat(3,1fr);

    gap: 12px;
}


.detail-box {

    padding:
        11px;

    border-radius: 10px;

    background:
        #0b1220;
}


.detail-label {

    color: var(--muted);

    font-size: 10px;

    text-transform: uppercase;
}


.detail-value {

    margin-top: 4px;

    font-weight: 750;

    font-size: 13px;
}


.chart-card {

    min-height: 280px;
}


.chart-bars {

    height: 190px;

    display: flex;

    align-items: end;

    gap: 14px;

    padding:
        20px 5px 5px;
}


.chart-column {

    flex: 1;

    height: 100%;

    display: flex;

    flex-direction: column;

    justify-content: end;

    align-items: center;

    gap: 7px;
}


.chart-bar {

    width: 100%;

    max-width: 48px;

    min-height: 4px;

    border-radius:
        8px 8px 3px 3px;

    background:
        linear-gradient(
            180deg,
            #60a5fa,
            #2563eb
        );

    box-shadow:
        0 7px 18px
        rgba(37,99,235,.18);

    transition:
        height .5s ease;
}


.chart-value {

    font-size: 10px;

    color: #cbd5e1;
}


.chart-label {

    font-size: 10px;

    color: var(--muted);
}


.progress-wrap {

    margin-top: 15px;
}


.progress {

    width: 100%;

    height: 11px;

    background:
        #1e293b;

    border-radius: 99px;

    overflow: hidden;
}


.progress span {

    display: block;

    height: 100%;

    background:
        linear-gradient(
            90deg,
            #22c55e,
            #3b82f6
        );

    border-radius: 99px;
}


.financial {

    display: grid;

    gap: 18px;
}


.financial-row {

    display: grid;

    grid-template-columns:
        90px 1fr 105px;

    align-items: center;

    gap: 10px;
}


.financial-track {

    height: 13px;

    background:
        #1e293b;

    border-radius: 99px;

    overflow: hidden;
}


.financial-fill {

    height: 100%;

    border-radius: 99px;
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

    display: grid;

    gap: 9px;
}


.recent-item {

    display: flex;

    justify-content: space-between;

    align-items: center;

    gap: 10px;

    padding: 12px;

    border-radius: 11px;

    background:
        #0b1220;

    border:
        1px solid var(--border);
}


.recent-name {

    font-weight: 750;

    font-size: 13px;
}


.recent-user {

    color: var(--muted);

    font-size: 11px;

    margin-top: 2px;
}


table {

    width: 100%;

    border-collapse:
        collapse;
}


th,
td {

    text-align: left;

    padding:
        12px;

    border-bottom:
        1px solid var(--border);

    font-size: 13px;
}


th {

    color:
        #94a3b8;

    font-weight:
        650;
}


.empty {

    text-align:
        center;

    padding:
        45px;

    color:
        var(--muted);
}


.mobile-menu {

    display: none;
}


@media(max-width:1100px) {

    .grid {

        grid-template-columns:
            repeat(2,1fr);
    }


    .client {

        grid-template-columns:
            1fr 1fr;
    }

}


@media(max-width:800px) {

    .sidebar {

        width:
            220px;

        transform:
            translateX(-100%);

        transition:
            .25s;

        box-shadow:
            15px 0 40px
            rgba(0,0,0,.30);
    }


    .sidebar.open {

        transform:
            translateX(0);
    }


    .mobile-menu {

        display:
            inline-flex;

        position:
            fixed;

        top: 14px;

        left: 14px;

        z-index: 200;

        width: 42px;

        height: 42px;

        border-radius: 11px;

        border:
            1px solid var(--border);

        background:
            #111827;

        color: white;

        align-items:
            center;

        justify-content:
            center;

        cursor:
            pointer;
    }


    .main {

        margin-left:
            0;

        width:
            100%;

        padding:
            65px 16px 25px;
    }


    .grid,
    .grid2,
    .form-grid {

        grid-template-columns:
            1fr;
    }


    .top {

        align-items:
            flex-start;

        flex-direction:
            column;
    }


    .client {

        grid-template-columns:
            1fr;
    }


    .drawer-content {

        grid-template-columns:
            1fr;
    }


    .financial-row {

        grid-template-columns:
            75px 1fr 90px;
    }


    .chart-bars {

        gap:
            8px;
    }

}

</style>

</head>

<body>

<button
    class="mobile-menu"
    onclick="document.querySelector('.sidebar').classList.toggle('open')"
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
    box-sizing:
        border-box;
}

body {

    margin: 0;

    min-height: 100vh;

    display: grid;

    place-items: center;

    padding: 20px;

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

    color: white;

    font-family:
        Inter,
        system-ui,
        sans-serif;
}

.login {

    width:
        min(420px,100%);

    background:
        rgba(17,24,39,.96);

    border:
        1px solid #243044;

    border-radius:
        22px;

    padding:
        32px;

    box-shadow:
        0 25px 80px
        rgba(0,0,0,.40);
}

.icon {

    width: 60px;
    height: 60px;

    margin: auto;

    border-radius:
        16px;

    display:
        grid;

    place-items:
        center;

    background:
        linear-gradient(
            135deg,
            #2563eb,
            #06b6d4
        );

    font-size:
        29px;
}

h1 {

    text-align:
        center;

    margin:
        15px 0 5px;

    font-size:
        26px;
}

.sub {

    text-align:
        center;

    color:
        #94a3b8;

    margin-bottom:
        25px;

    font-size:
        13px;
}

label {

    display:
        block;

    color:
        #cbd5e1;

    font-size:
        13px;

    margin:
        13px 0 7px;
}

input {

    width:
        100%;

    padding:
        13px;

    border-radius:
        10px;

    border:
        1px solid #243044;

    background:
        #0b1220;

    color:
        white;

    font-size:
        15px;

    outline:
        none;
}

input:focus {

    border-color:
        #3b82f6;
}

button {

    width:
        100%;

    margin-top:
        18px;

    padding:
        13px;

    border:
        0;

    border-radius:
        10px;

    background:
        linear-gradient(
            135deg,
            #3b82f6,
            #2563eb
        );

    color:
        white;

    font-weight:
        800;

    font-size:
        15px;

    cursor:
        pointer;
}

.error {

    background:
        rgba(239,68,68,.12);

    color:
        #fecaca;

    border:
        1px solid
        rgba(239,68,68,.20);

    padding:
        11px;

    border-radius:
        9px;

    margin-bottom:
        12px;

    font-size:
        13px;
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
            error="Usuário ou senha incorretos."
        )

    return render_template_string(
        LOGIN,
        error=None
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# INDEX
# ============================================================

@app.route("/")
def index():

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
# DASHBOARD
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():

    db = SessionLocal()

    try:

        resumo = obter_resumo(
            db
        )

        grafico_mensal = (
            obter_grafico_mensal(
                db
            )
        )

        recentes = (
            obter_clientes_recentes(
                db
            )
        )

        maior_mes = max(
            [
                item["valor"]
                for item in grafico_mensal
            ]
            or [1]
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

<div class="financial"
     style="margin-top:25px;">

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

<div
    style="
        display:grid;
        grid-template-columns:repeat(2,1fr);
        gap:10px;
        margin-top:18px;
    "
>

<div class="detail-box">

<div class="detail-label">
    Previsto
</div>

<div class="detail-value blue">
    {{ dinheiro(resumo.previsto) }}
</div>

</div>

<div class="detail-box">

<div class="detail-label">
    Pendente
</div>

<div class="detail-value yellow">
    {{ dinheiro(resumo.pendente_valor) }}
</div>

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

<div class="subtitle">
    Últimos cadastros realizados
</div>

<div
    class="recent"
    style="margin-top:15px;"
>

{% if recentes %}

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

<div>

<span
    class="badge
    {{ 'paid' if cliente.status == 'Pago'
       else 'pending' }}"
>

{{ cliente.status }}

</span>

</div>

</div>

{% endfor %}

{% else %}

<div class="empty">
    Nenhum cliente cadastrado.
</div>

{% endif %}

</div>

</div>

<div class="card">

<h3>
    ⚡ Ações rápidas
</h3>

<div class="subtitle">
    Atalhos para as funções principais
</div>

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
    href="{{ url_for('importar') }}"
>
    📥 Importar
</a>

<a
    class="btn secondary"
    href="{{ url_for('health') }}"
>
    🟢 Sistema
</a>

</div>

<div
    class="detail-box"
    style="margin-top:15px;"
>

<div class="detail-label">
    Receita prevista
</div>

<div class="metric blue">
    {{ dinheiro(resumo.previsto) }}
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
            grafico_mensal=grafico_mensal,
            recentes=recentes,
            maior_mes=maior_mes
        )

    except SQLAlchemyError as error:

        return (
            "Erro ao acessar o banco de dados: "
            + str(error),
            500
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

            termo = (
                f"%{busca}%"
            )

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

<div
    class="card"
    style="margin-bottom:18px;"
>

<form
    method="get"
    class="toolbar"
>

<div
    style="
        flex:2;
        min-width:220px;
    "
>

<label>
    Pesquisar cliente
</label>

<input
    name="busca"
    value="{{ busca }}"
    placeholder="Nome ou usuário..."
>

</div>

<div
    style="
        flex:1;
        min-width:160px;
    "
>

<label>
    Status
</label>

<select name="status">

<option
    value="Todos"
    {{ 'selected'
       if status == 'Todos' }}
>
    Todos
</option>

<option
    value="Pago"
    {{ 'selected'
       if status == 'Pago' }}
>
    Pagos
</option>

<option
    value="Pendente"
    {{ 'selected'
       if status == 'Pendente' }}
>
    Pendentes
</option>

</select>

</div>

<div
    style="
        align-self:end;
    "
>

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
        display:flex;
        justify-content:space-between;
        gap:10px;
        align-items:center;
        flex-wrap:wrap;
        color:#94a3b8;
        font-size:12px;
    "
>

<span>
    {{ lista|length }}
    cliente(s) encontrado(s)
</span>

<a
    href="{{ url_for('importar') }}"
    class="btn secondary"
>
    📥 Importar CSV
</a>

</div>

</div>

{% if lista %}

{% for c in lista %}

<div class="client">

<div>

<div
    class="name
    {{ 'green'
       if c.status == 'Pago'
       else 'yellow' }}"
>

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

<div>
    Cadastre um novo cliente ou importe um arquivo CSV.
</div>

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

    except SQLAlchemyError as error:

        return (
            "Erro ao acessar os clientes: "
            + str(error),
            500
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

<div
    class="actions mt"
>

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

            valor = float(
                request.form.get(
                    "valor",
                    "0"
                )
                or 0
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
                    url_for(
                        "novo_cliente"
                    )
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
                    url_for(
                        "novo_cliente"
                    )
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

        except (
            ValueError,
            SQLAlchemyError
        ):

            db.rollback()

            flash(
                "Não foi possível cadastrar o cliente."
            )

            return redirect(
                url_for(
                    "novo_cliente"
                )
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

            valor = float(
                request.form.get(
                    "valor",
                    "0"
                )
                or 0
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
                    url_for(
                        "editar_cliente",
                        cliente_id=cliente_id
                    )
                )

            outro = (
                db.query(Cliente)
                .filter(
                    Cliente.usuario
                    == usuario,
                    Cliente.id
                    != cliente_id
                )
                .first()
            )

            if outro:

                flash(
                    "Esse nome de usuário já pertence a outro cliente."
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

    except (
        ValueError,
        SQLAlchemyError
    ):

        db.rollback()

        flash(
            "Não foi possível atualizar o cliente."
        )

        return redirect(
            url_for("clientes")
        )

    finally:

        db.close()


# ============================================================
# ALTERAR STATUS / CONFIRMAR PAGAMENTO
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

        if cliente.status == "Pago":

            cliente.status = "Pendente"

            cliente.data_pagamento = None

            flash(
                "Cliente alterado para pendente."
            )

        else:

            # ==================================================
            # CONFIRMA PAGAMENTO
            # ==================================================

            cliente.status = "Pago"

            cliente.data_pagamento = (
                datetime.utcnow()
            )

            # ==================================================
            # NOVO VENCIMENTO
            #
            # O vencimento passa SEMPRE para o dia 10
            # do mês seguinte.
            #
            # Exemplos:
            #
            # 10/10/2026 -> 10/11/2026
            # 10/11/2026 -> 10/12/2026
            # 10/12/2026 -> 10/01/2027
            # ==================================================

            if cliente.vencimento:

                cliente.vencimento = (
                    calcular_proximo_vencimento(
                        cliente.vencimento
                    )
                )

            flash(
                "Pagamento registrado e vencimento atualizado para o dia 10 do próximo mês."
            )

        db.commit()

        return redirect(
            request.referrer
            or url_for("clientes")
        )

    except SQLAlchemyError:

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
# EXCLUIR CLIENTE
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
                Cliente.id
                == cliente_id
            )
            .first()
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

        return redirect(
            url_for("clientes")
        )

    except SQLAlchemyError:

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
                    "O CSV precisa ter as colunas Nome e Usuario."
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

                        if (
                            bruto
                            and bruto.lower()
                            != "nan"
                        ):

                            bruto = (
                                bruto
                                .replace(
                                    "R$",
                                    ""
                                )
                                .replace(
                                    " ",
                                    ""
                                )
                            )

                            if (
                                ","
                                in bruto
                                and "."
                                in bruto
                            ):

                                bruto = (
                                    bruto
                                    .replace(
                                        ".",
                                        ""
                                    )
                                    .replace(
                                        ",",
                                        "."
                                    )
                                )

                            elif "," in bruto:

                                bruto = bruto.replace(
                                    ",",
                                    "."
                                )

                            valor = float(
                                bruto
                            )

                    except (
                        ValueError,
                        TypeError
                    ):

                        valor = 0.0

                vencimento = None

                if "vencimento" in colunas:

                    try:

                        bruto = row[
                            colunas[
                                "vencimento"
                            ]
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
                "Não foi possível processar o arquivo CSV."
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

<div
    class="detail-box"
    style="margin:18px 0;"
>

<div class="detail-label">
    Colunas obrigatórias
</div>

<div
    class="detail-value"
    style="margin-top:8px;"
>

Nome, Usuario

</div>

<div
    class="detail-label"
    style="margin-top:12px;"
>

Colunas opcionais

</div>

<div
    class="detail-value"
    style="margin-top:8px;"
>

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

<div class="subtitle">
    Formato recomendado
</div>

<table
    style="margin-top:15px;"
>

<tr>

<th>
    Nome
</th>

<th>
    Usuario
</th>

<th>
    Valor
</th>

<th>
    Vencimento
</th>

</tr>

<tr>

<td>
    João Silva
</td>

<td>
    joao123
</td>

<td>
    25
</td>

<td>
    10/10/2026
</td>

</tr>

<tr>

<td>
    Maria Souza
</td>

<td>
    maria456
</td>

<td>
    40
</td>

<td>
    15/10/2026
</td>

</tr>

</table>

<div
    class="detail-box"
    style="margin-top:18px;"
>

<div class="detail-label">
    Observação
</div>

<div
    class="subtitle"
    style="margin-top:7px;"
>

Clientes com o mesmo usuário já existente serão ignorados.

</div>

</div>

</div>

</div>

"""

    return page(
        content,
        "Importar clientes",
        "importar"
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    db = SessionLocal()

    try:

        db.execute(
            text("SELECT 1")
        )

        return {
            "status": "ok",
            "database": "connected"
        }

    except Exception as error:

        return {
            "status": "error",
            "database": str(error)
        }, 500

    finally:

        db.close()


# ============================================================
# ERRO 404
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
# EXECUÇÃO LOCAL
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
