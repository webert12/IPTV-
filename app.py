import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, date
from pathlib import Path
import os
import hashlib
import hmac
import io

#
============================================================

CONFIGURAÇÃO

============================================================

st.set_page_config(
page_title="IPTV Manager",
page_icon="📺",
layout="wide",
initial_sidebar_state="expanded"
)

============================================================

ESTILO

============================================================

st.markdown(
"""
<style>
.stApp {
background-color: #0f1117;
}

    [data-testid="stSidebar"] {
        background-color: #151922;
    }

    .main-title {
        font-size: 32px;
        font-weight: 700;
        margin-bottom: 5px;
    }

    .subtitle {
        color: #9ca3af;
        font-size: 15px;
        margin-bottom: 25px;
    }

    .metric-card {
        background: #181c25;
        border: 1px solid #272d38;
        border-radius: 14px;
        padding: 20px;
        min-height: 125px;
    }

    .metric-title {
        color: #9ca3af;
        font-size: 14px;
    }

    .metric-value {
        font-size: 28px;
        font-weight: 700;
        margin-top: 8px;
    }

    .paid-name {
        color: #22c55e;
        font-weight: 700;
    }

    .pending-name {
        color: #facc15;
        font-weight: 700;
    }

    .section-title {
        font-size: 23px;
        font-weight: 700;
        margin-top: 10px;
        margin-bottom: 15px;
    }

    div[data-testid="stMetric"] {
        background-color: #181c25;
        border: 1px solid #272d38;
        padding: 15px;
        border-radius: 14px;
    }

    .stButton > button {
        border-radius: 9px;
    }

    .client-box {
        background-color: #181c25;
        border: 1px solid #272d38;
        border-radius: 12px;
        padding: 15px;
        margin-bottom: 10px;
    }

    footer {
        visibility: hidden;
    }
</style>
""",
unsafe_allow_html=True

)

============================================================

BANCO DE DADOS

============================================================

DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("DB_URL")

if not DATABASE_URL:
st.error(
"Banco de dados não configurado. "
"No Render, configure a variável DATABASE_URL."
)
st.stop()

Compatibilidade com algumas URLs antigas do Render

if DATABASE_URL.startswith("postgres://"):
DATABASE_URL = DATABASE_URL.replace(
"postgres://",
"postgresql://",
1
)

engine = create_engine(
DATABASE_URL,
pool_pre_ping=True,
pool_recycle=300
)

Base = declarative_base()
SessionLocal = sessionmaker(
bind=engine,
autocommit=False,
autoflush=False
)

============================================================

MODELO DE CLIENTE

============================================================

class Cliente(Base):
tablename = "clientes"

id = Column(Integer, primary_key=True, index=True)
nome = Column(String(150), nullable=False)
usuario = Column(String(150), nullable=False, unique=True, index=True)

valor = Column(Float, nullable=False, default=0.0)

vencimento = Column(Date, nullable=True)

status = Column(
    String(20),
    nullable=False,
    default="Pendente"
)

data_pagamento = Column(DateTime, nullable=True)

criado_em = Column(
    DateTime,
    nullable=False,
    default=datetime.utcnow
)

Base.metadata.create_all(bind=engine)

============================================================

LOGIN

============================================================

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

def verificar_login(usuario, senha):
return (
hmac.compare_digest(str(usuario), str(ADMIN_USER))
and hmac.compare_digest(str(senha), str(ADMIN_PASSWORD))
)

if "logado" not in st.session_state:
st.session_state.logado = False

def tela_login():

st.markdown(
    "<div style='height:80px'></div>",
    unsafe_allow_html=True
)

col1, col2, col3 = st.columns([1, 2, 1])

with col2:

    st.markdown(
        """
        <div style="
            text-align:center;
            background:#181c25;
            border:1px solid #272d38;
            border-radius:18px;
            padding:35px;
        ">
            <div style="font-size:55px;">📺</div>
            <h1>IPTV Manager</h1>
            <p style="color:#9ca3af;">
                Painel de gerenciamento de clientes
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.write("")

    usuario = st.text_input(
        "Usuário",
        placeholder="Digite o usuário"
    )

    senha = st.text_input(
        "Senha",
        type="password",
        placeholder="Digite a senha"
    )

    if st.button(
        "Entrar",
        use_container_width=True,
        type="primary"
    ):

        if verificar_login(usuario, senha):
            st.session_state.logado = True
            st.rerun()
        else:
            st.error("Usuário ou senha incorretos.")

if not st.session_state.logado:
tela_login()
st.stop()

============================================================

FUNÇÕES

============================================================

def obter_clientes():
db = SessionLocal()

try:
    return (
        db.query(Cliente)
        .order_by(Cliente.nome.asc())
        .all()
    )
finally:
    db.close()

def buscar_cliente(cliente_id):
db = SessionLocal()

try:
    return db.query(Cliente).filter(
        Cliente.id == cliente_id
    ).first()
finally:
    db.close()

def adicionar_cliente(nome, usuario, valor, vencimento):

db = SessionLocal()

try:

    usuario_existente = db.query(Cliente).filter(
        Cliente.usuario == usuario
    ).first()

    if usuario_existente:
        return False, "Esse nome de usuário já está cadastrado."

    cliente = Cliente(
        nome=nome.strip(),
        usuario=usuario.strip(),
        valor=float(valor),
        vencimento=vencimento,
        status="Pendente"
    )

    db.add(cliente)
    db.commit()

    return True, "Cliente cadastrado com sucesso."

except SQLAlchemyError as e:

    db.rollback()

    return False, f"Erro ao cadastrar cliente: {e}"

finally:
    db.close()

def atualizar_cliente(
cliente_id,
nome,
usuario,
valor,
vencimento
):

db = SessionLocal()

try:

    cliente = db.query(Cliente).filter(
        Cliente.id == cliente_id
    ).first()

    if not cliente:
        return False, "Cliente não encontrado."

    outro = db.query(Cliente).filter(
        Cliente.usuario == usuario,
        Cliente.id != cliente_id
    ).first()

    if outro:
        return False, "Esse nome de usuário já pertence a outro cliente."

    cliente.nome = nome.strip()
    cliente.usuario = usuario.strip()
    cliente.valor = float(valor)
    cliente.vencimento = vencimento

    db.commit()

    return True, "Cliente atualizado com sucesso."

except SQLAlchemyError as e:

    db.rollback()

    return False, f"Erro ao atualizar cliente: {e}"

finally:
    db.close()

def excluir_cliente(cliente_id):

db = SessionLocal()

try:

    cliente = db.query(Cliente).filter(
        Cliente.id == cliente_id
    ).first()

    if not cliente:
        return False, "Cliente não encontrado."

    db.delete(cliente)
    db.commit()

    return True, "Cliente excluído com sucesso."

except SQLAlchemyError as e:

    db.rollback()

    return False, f"Erro ao excluir cliente: {e}"

finally:
    db.close()

def alterar_status(cliente_id, status):

db = SessionLocal()

try:

    cliente = db.query(Cliente).filter(
        Cliente.id == cliente_id
    ).first()

    if not cliente:
        return False

    cliente.status = status

    if status == "Pago":
        cliente.data_pagamento = datetime.utcnow()
    else:
        cliente.data_pagamento = None

    db.commit()

    return True

except SQLAlchemyError:

    db.rollback()

    return False

finally:
    db.close()

def importar_clientes(df):

db = SessionLocal()

adicionados = 0
ignorados = 0

try:

    colunas = {
        str(c).strip().lower(): c
        for c in df.columns
    }

    if "nome" not in colunas or "usuario" not in colunas:
        return (
            0,
            0,
            "O arquivo precisa conter as colunas: Nome e Usuario."
        )

    coluna_nome = colunas["nome"]
    coluna_usuario = colunas["usuario"]

    coluna_valor = colunas.get("valor")
    coluna_vencimento = colunas.get("vencimento")

    for _, linha in df.iterrows():

        nome = str(linha[coluna_nome]).strip()
        usuario = str(linha[coluna_usuario]).strip()

        if not nome or not usuario:
            ignorados += 1
            continue

        existe = db.query(Cliente).filter(
            Cliente.usuario == usuario
        ).first()

        if existe:
            ignorados += 1
            continue

        valor = 0.0

        if coluna_valor:
            try:
                valor = float(
                    str(linha[coluna_valor])
                    .replace("R$", "")
                    .replace(".", "")
                    .replace(",", ".")
                    .strip()
                )
            except:
                valor = 0.0

        vencimento = None

        if coluna_vencimento:

            try:

                valor_data = linha[coluna_vencimento]

                if pd.notna(valor_data):

                    if isinstance(
                        valor_data,
                        (datetime, date)
                    ):
                        vencimento = (
                            valor_data.date()
                            if isinstance(valor_data, datetime)
                            else valor_data
                        )
                    else:
                        vencimento = pd.to_datetime(
                            valor_data,
                            dayfirst=True
                        ).date()

            except:
                vencimento = None

        cliente = Cliente(
            nome=nome,
            usuario=usuario,
            valor=valor,
            vencimento=vencimento,
            status="Pendente"
        )

        db.add(cliente)
        adicionados += 1

    db.commit()

    return (
        adicionados,
        ignorados,
        "Importação concluída."
    )

except Exception as e:

    db.rollback()

    return (
        adicionados,
        ignorados,
        f"Erro na importação: {e}"
    )

finally:
    db.close()

def dataframe_clientes():

clientes = obter_clientes()

dados = []

for c in clientes:

    dados.append(
        {
            "ID": c.id,
            "Nome": c.nome,
            "Usuário": c.usuario,
            "Valor": c.valor,
            "Vencimento": (
                c.vencimento.strftime("%d/%m/%Y")
                if c.vencimento
                else "-"
            ),
            "Status": c.status,
            "Data pagamento": (
                c.data_pagamento.strftime("%d/%m/%Y")
                if c.data_pagamento
                else "-"
            )
        }
    )

return pd.DataFrame(dados)

============================================================

SIDEBAR

============================================================

with st.sidebar:

st.markdown(
    """
    <div style="
        text-align:center;
        padding:10px 0 25px 0;
    ">
        <div style="font-size:48px;">📺</div>
        <h2 style="margin:0;">IPTV Manager</h2>
        <p style="color:#9ca3af;">
            Painel administrativo
        </p>
    </div>
    """,
    unsafe_allow_html=True
)

pagina = st.radio(
    "Menu",
    [
        "📊 Dashboard",
        "👥 Clientes",
        "➕ Adicionar Cliente",
        "📥 Importar Clientes"
    ]
)

st.divider()

if st.button(
    "🚪 Sair",
    use_container_width=True
):
    st.session_state.logado = False
    st.rerun()

============================================================

DASHBOARD

============================================================

if pagina == "📊 Dashboard":

clientes = obter_clientes()

total_clientes = len(clientes)

pagos = [
    c for c in clientes
    if c.status == "Pago"
]

pendentes = [
    c for c in clientes
    if c.status == "Pendente"
]

total_pago = sum(
    float(c.valor or 0)
    for c in pagos
)

total_pendente = sum(
    float(c.valor or 0)
    for c in pendentes
)

total_previsto = total_pago + total_pendente

st.markdown(
    '<div class="main-title">📊 Dashboard</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitle">'
    'Visão geral do seu sistema de clientes IPTV'
    '</div>',
    unsafe_allow_html=True
)

c1, c2, c3, c4 = st.columns(4)

with c1:
    st.metric(
        "👥 Total de clientes",
        total_clientes
    )

with c2:
    st.metric(
        "🟢 Clientes pagos",
        len(pagos)
    )

with c3:
    st.metric(
        "🟡 Pendentes",
        len(pendentes)
    )

with c4:
    st.metric(
        "💰 Total recebido",
        f"R$ {total_pago:,.2f}".replace(
            ",", "X"
        ).replace(
            ".", ","
        ).replace(
            "X", "."
        )
    )

st.write("")

c5, c6 = st.columns(2)

with c5:

    st.markdown(
        '<div class="section-title">'
        '💵 Financeiro'
        '</div>',
        unsafe_allow_html=True
    )

    st.metric(
        "Valor pendente",
        f"R$ {total_pendente:,.2f}".replace(
            ",", "X"
        ).replace(
            ".", ","
        ).replace(
            "X", "."
        )
    )

with c6:

    st.markdown(
        '<div class="section-title">'
        '📈 Faturamento previsto'
        '</div>',
        unsafe_allow_html=True
    )

    st.metric(
        "Total previsto",
        f"R$ {total_previsto:,.2f}".replace(
            ",", "X"
        ).replace(
            ".", ","
        ).replace(
            "X", "."
        )
    )

st.write("")

# --------------------------------------------------------
# GRÁFICO PAGOS X PENDENTES
# --------------------------------------------------------

col1, col2 = st.columns(2)

with col1:

    st.markdown(
        '<div class="section-title">'
        '📊 Pagos x Pendentes'
        '</div>',
        unsafe_allow_html=True
    )

    dados_status = pd.DataFrame(
        {
            "Status": [
                "Pagos",
                "Pendentes"
            ],
            "Quantidade": [
                len(pagos),
                len(pendentes)
            ]
        }
    )

    if total_clientes > 0:

        fig = px.pie(
            dados_status,
            names="Status",
            values="Quantidade",
            hole=0.55
        )

        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(
                l=10,
                r=10,
                t=20,
                b=20
            )
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

    else:

        st.info(
            "Ainda não existem clientes cadastrados."
        )

with col2:

    st.markdown(
        '<div class="section-title">'
        '💰 Valores'
        '</div>',
        unsafe_allow_html=True
    )

    dados_valores = pd.DataFrame(
        {
            "Status": [
                "Recebido",
                "Pendente"
            ],
            "Valor": [
                total_pago,
                total_pendente
            ]
        }
    )

    fig2 = px.bar(
        dados_valores,
        x="Status",
        y="Valor",
        text_auto=".2f"
    )

    fig2.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(
            l=10,
            r=10,
            t=20,
            b=20
        ),
        yaxis_title="Valor (R$)",
        xaxis_title=""
    )

    st.plotly_chart(
        fig2,
        use_container_width=True
    )

# --------------------------------------------------------
# CLIENTES RECENTES
# --------------------------------------------------------

st.markdown(
    '<div class="section-title">'
    '👥 Clientes'
    '</div>',
    unsafe_allow_html=True
)

df = dataframe_clientes()

if not df.empty:

    st.dataframe(
        df[
            [
                "Nome",
                "Usuário",
                "Valor",
                "Vencimento",
                "Status"
            ]
        ],
        use_container_width=True,
        hide_index=True
    )

else:

    st.info(
        "Nenhum cliente cadastrado."
    )

============================================================

CLIENTES

============================================================

elif pagina == "👥 Clientes":

st.markdown(
    '<div class="main-title">👥 Clientes</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitle">'
    'Gerencie seus clientes, pagamentos e pendências.'
    '</div>',
    unsafe_allow_html=True
)

clientes = obter_clientes()

pesquisa = st.text_input(
    "🔎 Pesquisar cliente",
    placeholder="Digite o nome ou usuário..."
)

status_filtro = st.selectbox(
    "Filtrar por status",
    [
        "Todos",
        "Pago",
        "Pendente"
    ]
)

clientes_filtrados = clientes

if pesquisa:

    termo = pesquisa.lower()

    clientes_filtrados = [
        c for c in clientes_filtrados
        if termo in c.nome.lower()
        or termo in c.usuario.lower()
    ]

if status_filtro != "Todos":

    clientes_filtrados = [
        c for c in clientes_filtrados
        if c.status == status_filtro
    ]

st.write(
    f"**{len(clientes_filtrados)}** cliente(s) encontrado(s)"
)

for cliente in clientes_filtrados:

    cor = (
        "#22c55e"
        if cliente.status == "Pago"
        else "#facc15"
    )

    with st.container(border=True):

        col1, col2, col3, col4 = st.columns(
            [3, 2, 1.5, 2]
        )

        with col1:

            st.markdown(
                f"""
                <div style="
                    color:{cor};
                    font-size:18px;
                    font-weight:700;
                ">
                    {cliente.nome}
                </div>
                """,
                unsafe_allow_html=True
            )

            st.caption(
                f"Usuário: {cliente.usuario}"
            )

        with col2:

            st.write(
                f"**R$ {cliente.valor:,.2f}"
                .replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )

            if cliente.vencimento:
                st.caption(
                    f"Vencimento: "
                    f"{cliente.vencimento.strftime('%d/%m/%Y')}"
                )

        with col3:

            if cliente.status == "Pago":
                st.success("Pago")
            else:
                st.warning("Pendente")

        with col4:

            b1, b2 = st.columns(2)

            with b1:

                if cliente.status == "Pago":

                    if st.button(
                        "⏳ Pendente",
                        key=f"pend_{cliente.id}",
                        use_container_width=True
                    ):

                        alterar_status(
                            cliente.id,
                            "Pendente"
                        )

                        st.rerun()

                else:

                    if st.button(
                        "💰 Pagar",
                        key=f"pagar_{cliente.id}",
                        use_container_width=True
                    ):

                        alterar_status(
                            cliente.id,
                            "Pago"
                        )

                        st.rerun()

            with b2:

                if st.button(
                    "⚙️",
                    key=f"editar_{cliente.id}",
                    use_container_width=True
                ):

                    st.session_state[
                        "editar_cliente"
                    ] = cliente.id

        if st.session_state.get(
            "editar_cliente"
        ) == cliente.id:

            st.divider()

            st.markdown(
                "### ✏️ Editar cliente"
            )

            with st.form(
                f"form_editar_{cliente.id}"
            ):

                nome = st.text_input(
                    "Nome",
                    value=cliente.nome
                )

                usuario = st.text_input(
                    "Nome de usuário",
                    value=cliente.usuario
                )

                valor = st.number_input(
                    "Valor da mensalidade",
                    min_value=0.0,
                    value=float(cliente.valor),
                    step=1.0
                )

                vencimento = st.date_input(
                    "Data de vencimento",
                    value=(
                        cliente.vencimento
                        if cliente.vencimento
                        else date.today()
                    )
                )

                c1, c2 = st.columns(2)

                with c1:

                    salvar = st.form_submit_button(
                        "💾 Salvar alterações",
                        use_container_width=True,
                        type="primary"
                    )

                with c2:

                    cancelar = st.form_submit_button(
                        "Cancelar",
                        use_container_width=True
                    )

                if salvar:

                    sucesso, mensagem = atualizar_cliente(
                        cliente.id,
                        nome,
                        usuario,
                        valor,
                        vencimento
                    )

                    if sucesso:

                        st.success(mensagem)

                        st.session_state.pop(
                            "editar_cliente",
                            None
                        )

                        st.rerun()

                    else:

                        st.error(mensagem)

                if cancelar:

                    st.session_state.pop(
                        "editar_cliente",
                        None
                    )

                    st.rerun()

            confirmar = st.checkbox(
                "Confirmar exclusão deste cliente",
                key=f"confirmar_{cliente.id}"
            )

            if confirmar:

                if st.button(
                    "🗑️ Excluir definitivamente",
                    key=f"excluir_{cliente.id}",
                    type="secondary"
                ):

                    sucesso, mensagem = excluir_cliente(
                        cliente.id
                    )

                    if sucesso:

                        st.success(mensagem)

                        st.session_state.pop(
                            "editar_cliente",
                            None
                        )

                        st.rerun()

                    else:

                        st.error(mensagem)

============================================================

ADICIONAR CLIENTE

============================================================

elif pagina == "➕ Adicionar Cliente":

st.markdown(
    '<div class="main-title">'
    '➕ Adicionar Cliente'
    '</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitle">'
    'Cadastre um novo cliente no sistema.'
    '</div>',
    unsafe_allow_html=True
)

with st.form("novo_cliente"):

    nome = st.text_input(
        "Nome do cliente",
        placeholder="Ex: João Silva"
    )

    usuario = st.text_input(
        "Nome de usuário",
        placeholder="Ex: joao123"
    )

    c1, c2 = st.columns(2)

    with c1:

        valor = st.number_input(
            "Valor da mensalidade",
            min_value=0.0,
            value=25.0,
            step=1.0
        )

    with c2:

        vencimento = st.date_input(
            "Data de vencimento",
            value=date.today()
        )

    salvar = st.form_submit_button(
        "💾 Cadastrar cliente",
        use_container_width=True,
        type="primary"
    )

    if salvar:

        if not nome.strip():

            st.error(
                "Digite o nome do cliente."
            )

        elif not usuario.strip():

            st.error(
                "Digite o nome de usuário."
            )

        else:

            sucesso, mensagem = adicionar_cliente(
                nome,
                usuario,
                valor,
                vencimento
            )

            if sucesso:

                st.success(mensagem)

            else:

                st.error(mensagem)

============================================================

IMPORTAR CLIENTES

============================================================

elif pagina == "📥 Importar Clientes":

st.markdown(
    '<div class="main-title">'
    '📥 Importar Clientes'
    '</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitle">'
    'Adicione vários clientes de uma única vez.'
    '</div>',
    unsafe_allow_html=True
)

st.info(
    "O arquivo CSV deve possuir pelo menos as colunas "
    "**Nome** e **Usuario**. "
    "As colunas **Valor** e **Vencimento** são opcionais."
)

st.markdown("### 📄 Formato do arquivo")

exemplo = pd.DataFrame(
    {
        "Nome": [
            "João Silva",
            "Maria Souza"
        ],
        "Usuario": [
            "joao123",
            "maria456"
        ],
        "Valor": [
            25,
            40
        ],
        "Vencimento": [
            "10/10/2026",
            "15/10/2026"
        ]
    }
)

st.dataframe(
    exemplo,
    use_container_width=True,
    hide_index=True
)

arquivo = st.file_uploader(
    "Escolha um arquivo CSV",
    type=["csv"]
)

if arquivo:

    try:

        df_importacao = pd.read_csv(
            arquivo,
            sep=None,
            engine="python"
        )

        st.write(
            f"**{len(df_importacao)}** registros encontrados."
        )

        st.dataframe(
            df_importacao,
            use_container_width=True,
            hide_index=True
        )

        if st.button(
            "📥 Importar clientes",
            type="primary",
            use_container_width=True
        ):

            adicionados, ignorados, mensagem = (
                importar_clientes(
                    df_importacao
                )
            )

            if adicionados > 0:
                st.success(
                    f"{adicionados} cliente(s) "
                    f"adicionado(s) com sucesso."
                )

            if ignorados > 0:
                st.warning(
                    f"{ignorados} registro(s) "
                    f"foram ignorados."
                )

            if adicionados == 0:
                st.error(mensagem)

            st.rerun()

    except Exception as e:

        st.error(
            f"Não foi possível ler o arquivo: {e}"
        )

============================================================

RODAPÉ

============================================================

st.sidebar.markdown(
"""
<div style="
position:fixed;
bottom:15px;
color:#6b7280;
font-size:12px;
">
IPTV Manager • Administração
</div>
""",
unsafe_allow_html=True
)
