import os
from datetime import datetime, date
from functools import wraps

from flask import Flask, request, redirect, url_for, session, render_template_string, flash
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, func
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import SQLAlchemyError

app = Flask(__name__)

app.secret_key = os.getenv(
    "SECRET_KEY",
    "change-this-secret-key"
)

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DB_URL")

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
    autocommit=False,
)


ADMIN_USER = os.getenv(
    "ADMIN_USER",
    "admin"
)

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD",
    "admin123"
)


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


def dinheiro(valor):

    return (
        f"R$ {float(valor or 0):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def obter_resumo(db):

    total = (
        db.query(
            func.count(Cliente.id)
        ).scalar()
        or 0
    )

    pagos = (
        db.query(
            func.count(Cliente.id)
        )
        .filter(
            Cliente.status == "Pago"
        )
        .scalar()
        or 0
    )

    pendentes = (
        db.query(
            func.count(Cliente.id)
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
                func.sum(Cliente.valor),
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
                func.sum(Cliente.valor),
                0
            )
        )
        .filter(
            Cliente.status == "Pendente"
        )
        .scalar()
        or 0
    )

    return {
        "total": total,
        "pagos": pagos,
        "pendentes": pendentes,
        "recebido": float(recebido),
        "pendente_valor": float(
            pendente_valor
        ),
        "previsto": float(recebido)
        + float(pendente_valor),
    }


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

:root{

    --bg:#0b0f17;

    --panel:#111827;

    --panel2:#151e2d;

    --border:#243044;

    --text:#f8fafc;

    --muted:#94a3b8;

    --blue:#3b82f6;

    --green:#22c55e;

    --yellow:#facc15;

    --red:#ef4444;

    --shadow:
        0 16px 40px rgba(0,0,0,.25);
}


*{
    box-sizing:border-box;
}


body{

    margin:0;

    background:
        linear-gradient(
            135deg,
            #080c13,
            #0f172a
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


a{
    text-decoration:none;
    color:inherit;
}


.layout{

    min-height:100vh;

    display:flex;
}


.sidebar{

    width:250px;

    background:
        rgba(10,15,24,.94);

    border-right:
        1px solid var(--border);

    padding:24px 16px;

    position:fixed;

    inset:0 auto 0 0;

    z-index:10;
}


.brand{

    display:flex;

    gap:12px;

    align-items:center;

    padding:
        8px 10px 28px;
}


.brand-icon{

    width:42px;

    height:42px;

    border-radius:12px;

    background:
        linear-gradient(
            135deg,
            #2563eb,
            #06b6d4
        );

    display:grid;

    place-items:center;

    font-size:22px;
}


.brand strong{

    display:block;

    font-size:18px;
}


.brand span{

    font-size:12px;

    color:var(--muted);
}


.nav-title{

    font-size:11px;

    color:#64748b;

    text-transform:uppercase;

    letter-spacing:1px;

    padding:
        0 12px 8px;
}


.nav a{

    display:block;

    padding:12px;

    border-radius:10px;

    color:#cbd5e1;

    margin:4px 0;
}


.nav a:hover,
.nav a.active{

    background:#172236;

    color:#fff;
}


.logout{

    position:absolute;

    left:16px;

    right:16px;

    bottom:20px;
}


.logout a{

    display:block;

    text-align:center;

    padding:11px;

    border:
        1px solid var(--border);

    border-radius:10px;

    color:#cbd5e1;
}


.main{

    margin-left:250px;

    width:
        calc(100% - 250px);

    padding:30px;

    max-width:1500px;
}


.top{

    display:flex;

    justify-content:space-between;

    align-items:center;

    gap:15px;

    margin-bottom:28px;
}


h1{

    font-size:30px;

    margin:
        0 0 5px;
}


.subtitle{

    color:var(--muted);
}


.grid{

    display:grid;

    grid-template-columns:
        repeat(4,1fr);

    gap:16px;

    margin-bottom:18px;
}


.grid2{

    display:grid;

    grid-template-columns:
        repeat(2,1fr);

    gap:18px;
}


.card{

    background:
        rgba(17,24,39,.86);

    border:
        1px solid var(--border);

    border-radius:16px;

    padding:20px;

    box-shadow:var(--shadow);
}


.metric-label{

    color:var(--muted);

    font-size:13px;
}


.metric{

    font-size:27px;

    font-weight:800;

    margin-top:8px;
}


.green{
    color:var(--green);
}


.yellow{
    color:var(--yellow);
}


.red{
    color:var(--red);
}


.blue{
    color:#60a5fa;
}


.toolbar{

    display:flex;

    gap:10px;

    flex-wrap:wrap;

    margin-bottom:18px;
}


input,
select{

    width:100%;

    background:#0b1220;

    color:#fff;

    border:
        1px solid var(--border);

    border-radius:10px;

    padding:12px;

    font:inherit;

    outline:none;
}


input:focus,
select:focus{

    border-color:#3b82f6;
}


.form-grid{

    display:grid;

    grid-template-columns:
        repeat(2,1fr);

    gap:16px;
}


label{

    display:block;

    color:#cbd5e1;

    font-size:13px;

    margin-bottom:7px;
}


.btn{

    border:0;

    border-radius:10px;

    padding:11px 15px;

    font:inherit;

    font-weight:700;

    cursor:pointer;

    display:inline-block;
}


.primary{

    background:var(--blue);

    color:white;
}


.secondary{

    background:#1e293b;

    color:white;

    border:
        1px solid var(--border);
}


.success{

    background:#14532d;

    color:#bbf7d0;
}


.danger{

    background:#451a1a;

    color:#fecaca;
}


.actions{

    display:flex;

    gap:7px;

    flex-wrap:wrap;
}


.client{

    display:grid;

    grid-template-columns:
        2fr 1fr 1fr 1fr auto;

    gap:15px;

    align-items:center;

    padding:16px;

    border:
        1px solid var(--border);

    border-radius:14px;

    background:#101827;

    margin-bottom:10px;
}


.name{

    font-size:17px;

    font-weight:800;
}


.username{

    font-size:13px;

    color:var(--muted);

    margin-top:3px;
}


.badge{

    display:inline-block;

    padding:6px 9px;

    border-radius:999px;

    font-size:12px;

    font-weight:800;
}


.badge.paid{

    background:
        rgba(34,197,94,.13);

    color:#4ade80;
}


.badge.pending{

    background:
        rgba(250,204,21,.13);

    color:#fde047;
}


.flash{

    padding:12px 15px;

    border-radius:10px;

    background:#172554;

    border:
        1px solid #1d4ed8;

    margin-bottom:18px;
}


table{

    width:100%;

    border-collapse:collapse;
}


th,
td{

    text-align:left;

    padding:12px;

    border-bottom:
        1px solid var(--border);

    font-size:14px;
}


th{

    color:#94a3b8;

    font-weight:600;
}


.empty{

    text-align:center;

    padding:40px;

    color:var(--muted);
}


.mt{

    margin-top:16px;
}


.full{

    width:100%;
}


.bar{

    height:12px;

    background:#1e293b;

    border-radius:99px;

    overflow:hidden;

    margin-top:12px;
}


.bar span{

    display:block;

    height:100%;

    background:
        linear-gradient(
            90deg,
            #22c55e,
            #3b82f6
        );
}


@media(max-width:1000px){

    .grid{

        grid-template-columns:
            repeat(2,1fr);
    }

    .client{

        grid-template-columns:
            1fr 1fr;
    }

    .main{

        padding:20px;
    }
}


@media(max-width:700px){

    .sidebar{

        width:100%;

        height:auto;

        position:relative;

        border-right:0;

        border-bottom:
            1px solid var(--border);
    }

    .layout{

        display:block;
    }

    .main{

        margin-left:0;

        width:100%;

        padding:16px;
    }

    .logout{

        position:static;

        margin-top:20px;
    }

    .grid,
    .grid2,
    .form-grid{

        grid-template-columns:1fr;
    }

    .top{

        align-items:flex-start;

        flex-direction:column;
    }

    .client{

        grid-template-columns:1fr;
    }

    .nav{

        display:grid;

        grid-template-columns:
            1fr 1fr;
    }

    .brand{

        padding-bottom:15px;
    }
}

</style>

</head>

<body>

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
Menu
</div>


<nav class="nav">

<a
class="{{ 'active' if active=='dashboard' else '' }}"
href="{{ url_for('dashboard') }}"
>
📊 Dashboard
</a>


<a
class="{{ 'active' if active=='clientes' else '' }}"
href="{{ url_for('clientes') }}"
>
👥 Clientes
</a>


<a
class="{{ 'active' if active=='novo' else '' }}"
href="{{ url_for('novo_cliente') }}"
>
➕ Adicionar
</a>


<a
class="{{ 'active' if active=='importar' else '' }}"
href="{{ url_for('importar') }}"
>
📥 Importar
</a>

</nav>


<div class="logout">

<a href="{{ url_for('logout') }}">
🚪 Sair
</a>

</div>

</aside>


<main class="main">

{% with messages=get_flashed_messages() %}

{% for message in messages %}

<div class="flash">
{{ message }}
</div>

{% endfor %}

{% endwith %}


{{ content|safe }}

</main>

</div>

</body>

</html>
"""


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

*{
    box-sizing:border-box;
}


body{

    margin:0;

    min-height:100vh;

    display:grid;

    place-items:center;

    background:
        linear-gradient(
            135deg,
            #080c13,
            #0f172a
        );

    color:#fff;

    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
}


.login{

    width:min(
        410px,
        92%
    );

    background:#111827;

    border:
        1px solid #243044;

    border-radius:20px;

    padding:32px;

    box-shadow:
        0 20px 60px #0006;
}


.icon{

    width:58px;

    height:58px;

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


h1{

    text-align:center;

    margin:
        15px 0 5px;
}


.sub{

    text-align:center;

    color:#94a3b8;

    margin-bottom:25px;
}


label{

    display:block;

    color:#cbd5e1;

    font-size:13px;

    margin:
        13px 0 7px;
}


input{

    width:100%;

    padding:12px;

    border-radius:10px;

    border:
        1px solid #243044;

    background:#0b1220;

    color:#fff;

    font-size:15px;
}


button{

    width:100%;

    margin-top:18px;

    padding:12px;

    border:0;

    border-radius:10px;

    background:#3b82f6;

    color:#fff;

    font-weight:800;

    font-size:15px;

    cursor:pointer;
}


.error{

    background:#451a1a;

    color:#fecaca;

    padding:10px;

    border-radius:9px;

    margin-bottom:12px;
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
>


<label>
Senha
</label>

<input
type="password"
name="senha"
autocomplete="current-password"
required
>


<button>
Entrar
</button>

</form>

</div>

</body>

</html>
"""


def page(
    content,
    title,
    active
):

    return render_template_string(
        BASE,
        content=render_template_string(
            content
        ),
        title=title,
        active=active
    )


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


@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


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


@app.route("/dashboard")
@login_required
def dashboard():

    db = SessionLocal()

    try:

        resumo = obter_resumo(db)

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

</div>


<div class="grid">


<div class="card">

<div class="metric-label">
👥 Total de clientes
</div>

<div class="metric">
{{ resumo.total }}
</div>

</div>


<div class="card">

<div class="metric-label">
🟢 Clientes pagos
</div>

<div class="metric green">
{{ resumo.pagos }}
</div>

</div>


<div class="card">

<div class="metric-label">
🟡 Pendentes
</div>

<div class="metric yellow">
{{ resumo.pendentes }}
</div>

</div>


<div class="card">

<div class="metric-label">
💰 Total recebido
</div>

<div class="metric blue">
{{ dinheiro(resumo.recebido) }}
</div>

</div>


</div>


<div class="grid2">


<div class="card">

<div class="metric-label">
💵 Valor pendente
</div>

<div class="metric">
{{ dinheiro(resumo.pendente_valor) }}
</div>

</div>


<div class="card">

<div class="metric-label">
📈 Faturamento previsto
</div>

<div class="metric">
{{ dinheiro(resumo.previsto) }}
</div>

</div>


</div>


<div
class="grid2"
style="margin-top:18px"
>


<div class="card">

<h3>
Distribuição de clientes
</h3>


{% set percentual =
(
resumo.pagos
/
resumo.total
*
100
)
if resumo.total
else 0
%}


<div
style="
font-size:30px;
font-weight:800
"
>

{{ "%.0f"|format(percentual) }}%

</div>


<div class="subtitle">
dos clientes estão pagos
</div>


<div class="bar">

<span
style="width:{{ percentual }}%"
>
</span>

</div>


<div
style="
display:flex;
justify-content:space-between;
margin-top:15px;
color:#94a3b8;
font-size:13px
"
>

<span>
Pagos: {{ resumo.pagos }}
</span>

<span>
Pendentes: {{ resumo.pendentes }}
</span>

</div>

</div>


<div class="card">

<h3>
Resumo financeiro
</h3>


<table>

<tr>

<td>
Recebido
</td>

<td class="green">

<strong>
{{ dinheiro(resumo.recebido) }}
</strong>

</td>

</tr>


<tr>

<td>
Pendente
</td>

<td class="yellow">

<strong>
{{ dinheiro(resumo.pendente_valor) }}
</strong>

</td>

</tr>


<tr>

<td>
Total previsto
</td>

<td class="blue">

<strong>
{{ dinheiro(resumo.previsto) }}
</strong>

</td>

</tr>

</table>

</div>

</div>

"""

        return page(
            content,
            "Dashboard",
            "dashboard"
        )

    finally:

        db.close()


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


<div
class="card"
style="margin-bottom:18px"
>

<form
method="get"
class="toolbar"
>


<div
style="
flex:2;
min-width:220px
"
>

<label>
Pesquisar
</label>

<input
name="busca"
value="{{ busca }}"
placeholder="Nome ou usuário"
>

</div>


<div
style="
flex:1;
min-width:160px
"
>

<label>
Status
</label>

<select
name="status"
>

<option
{{ 'selected' if status=='Todos' }}
>
Todos
</option>

<option
{{ 'selected' if status=='Pago' }}
>
Pago
</option>

<option
{{ 'selected' if status=='Pendente' }}
>
Pendente
</option>

</select>

</div>


<div
style="align-self:end"
>

<button
class="btn primary"
>
Filtrar
</button>

</div>


</form>

</div>


<div
class="subtitle"
style="margin-bottom:12px"
>

{{ lista|length }}
cliente(s) encontrado(s)

</div>


{% if lista %}


{% for c in lista %}


<div class="client">


<div>

<div
class="name
{{ 'green'
if c.status=='Pago'
else 'yellow'
}}"
>

{{ c.nome }}

</div>

<div class="username">

{{ c.usuario }}

</div>

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

{{ c.vencimento.strftime('%d/%m/%Y')
if c.vencimento
else '-' }}

</strong>

</div>


<div>

<span
class="badge
{{ 'paid'
if c.status=='Pago'
else 'pending'
}}"
>

{{ '✓ Pago'
if c.status=='Pago'
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
if c.status=='Pago'
else 'success'
}}"
>

{{ 'Marcar pendente'
if c.status=='Pago'
else 'Marcar pago'
}}

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
)
"
>

<button
class="btn danger"
>
Excluir
</button>

</form>


</div>


</div>


{% endfor %}


{% else %}


<div class="card empty">

Nenhum cliente encontrado.

</div>


{% endif %}

"""

        return page(
            content,
            "Clientes",
            "clientes"
        )

    finally:

        db.close()


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
placeholder="Ex: João Silva"
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
placeholder="Ex: joao123"
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
>

{{ 'Salvar alterações'
if editar
else 'Cadastrar cliente' }}

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

            vencimento_texto = request.form.get(
                "vencimento",
                ""
            ).strip()


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


            db.add(
                Cliente(
                    nome=nome,
                    usuario=usuario,
                    valor=valor,
                    vencimento=vencimento,
                    status="Pendente"
                )
            )


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


    return render_template_string(
        BASE,
        content=render_template_string(
            FORM,
            c=None,
            editar=False
        ),
        title="Adicionar cliente",
        active="novo"
    )


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

            vencimento_texto = request.form.get(
                "vencimento",
                ""
            ).strip()


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


        return render_template_string(
            BASE,
            content=render_template_string(
                FORM,
                c=cliente,
                editar=True
            ),
            title="Editar cliente",
            active="clientes"
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

        else:

            cliente.status = "Pago"

            cliente.data_pagamento = datetime.utcnow()


        db.commit()


        flash(
            "Status atualizado."
        )


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
                    row[colunas["nome"]]
                ).strip()

                usuario = str(
                    row[colunas["usuario"]]
                ).strip()


                if (
                    not nome
                    or not usuario
                    or nome.lower()
                    == "nan"
                    or usuario.lower()
                    == "nan"
                ):

                    ignorados += 1

                    continue


                existe = (
                    db.query(Cliente)
                    .filter(
                        Cliente.usuario
                        == usuario
                    )
                    .first()
                )


                if existe:

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
                            bruto.lower()
                            != "nan"
                            and bruto
                        ):

                            valor = float(

                                bruto
                                .replace(
                                    "R$",
                                    ""
                                )
                                .replace(
                                    " ",
                                    ""
                                )
                                .replace(
                                    ".",
                                    ""
                                )
                                .replace(
                                    ",",
                                    "."
                                )

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
                            colunas["vencimento"]
                        ]


                        if (
                            bruto
                            and str(bruto).lower()
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
                "Importação concluída: "
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

</div>


<div class="grid2">


<div class="card">

<h3>
Arquivo CSV
</h3>


<p class="subtitle">

Colunas obrigatórias:

<strong>
Nome
</strong>

e

<strong>
Usuario
</strong>.

Valor e Vencimento são opcionais.

</p>


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
>

📥 Importar clientes

</button>

</form>

</div>


<div class="card">

<h3>
Exemplo
</h3>


<table>

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

</div>

</div>

"""


    return page(
        content,
        "Importar clientes",
        "importar"
    )


@app.errorhandler(404)
def not_found(error):

    return redirect(
        url_for(
            "dashboard"
            if session.get("logged_in")
            else "login"
        )
    )


if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
