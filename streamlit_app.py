import re
import sqlite3
from io import BytesIO
from pathlib import Path
from typing import List

import pandas as pd
import qrcode
import streamlit as st

# =========================================================
# SMARTLABS @ CPCecho - Awards App
# =========================================================
# Instalar:
#   pip install streamlit pandas qrcode pillow
#
# Correr:
#   streamlit run streamlit_app.py
#
# O que esta app faz:
# - Votação por telemóvel com QR code
# - 10 categorias
# - 1 voto por pessoa em cada categoria
# - Mostra apenas 1 pergunta de cada vez ao votante
# - Página de apresentação mostra apenas 1 categoria de cada vez
# - Os resultados só aparecem quando clicas em "Ver resultados"
# - Página final com resumo completo
# - Página admin para controlar a apresentação
# =========================================================

st.set_page_config(
    page_title="CPCecho Awards by SmartLabs",
    page_icon="🏆",
    layout="wide",
)
query_params = st.query_params
mode = query_params.get("mode", "full")


# -----------------------------
# CONFIGURAÇÃO GERAL
# -----------------------------
APP_TITLE = "CPCecho Awards"
APP_SUBTITLE = "Powered by SmartLabs @ CPCecho 😎"
DB_PATH = Path("cpcecho_awards.db")

# Coloca aqui o link público quando tiveres um.
APP_URL = "https://cpcecho-awards.streamlit.app/?mode=vote"

# Código de admin para controlar apresentação e exportações.
ADMIN_CODE = "cpcecho2026"

# Se quiseres obrigar email da empresa, muda para True.
EMAIL_DOMAIN = "cpcecho.com"
REQUIRE_COMPANY_EMAIL = False

# Se quiseres mostrar percentagens nas tabelas finais.
SHOW_PERCENTAGES = True

# -----------------------------
# CATEGORIAS
# -----------------------------
CATEGORIES: List[str] = [
    "Sentido de Compromisso de Ferro 🤝",
    "Cérebro da Equipa (Mais Competência) 🧠",
    "Coração da Equipa (Mais Espírito de Equipa) ❤️",
    "Mau Feitio Oficial 😈",
    "Boa Onda da Equipa 😇",
    "Motor da Evolução 🚀",
    "Pessoa Mais Confiável 🔒",
    "Megafone da Comunicação 📣",
    "Mais Nhonhinha da Equipa 🧸",
    "Resolve Tudo Antes de Ser Problema 🛠️",
]

# -----------------------------
# COLABORADORES
# -----------------------------
EMPLOYEES: List[str] = [
    "Alfredo Fernandes",
    "António Costa",
    "António Parente",
    "Bruno Santos",
    "Carla Alves",
    "Carlos Guimarães",
    "Daniela Cunha",
    "Diana Neves",
    "Diogo Cruz",
    "Fernando Matos Pereira",
    "Filipe Cerqueira",
    "Francisco Monteiro",
    "Frederico Gonçalves",
    "Hugo Moura",
    "Jesse Arce",
    "Joana Azevedo",
    "Joana Rodrigues",
    "João Castilho",
    "João Ferreira",
    "João Silva",
    "João Teixeira",
    "Jorge Miranda",
    "Jorge Queiroz Machado",
    "José Carlos Silva",
    "José Manuel Pires",
    "Luísa Cortez",
    "Marcio Sousa",
    "Miguel Fonseca",
    "Miguel Mota",
    "Monica Pinto",
    "Nuno Duarte",
    "Nuno Fernandes",
    "Nuno Gomes",
    "Nuno Guimarães",
    "Olga Quintais",
    "Patricia Lima",
    "Pedro Andreso",
    "Pedro Ribeiro",
    "Ricardo Sousa",
    "Rui Bandeira",
    "Rui Caldas",
    "Sandra Silva",
    "Sergio Canelas",
    "Silvia Martins",
    "Susana Costa",
]

# Se quiseres limitar nomeados por categoria, podes usar isto.
# Se ficar vazio, toda a gente pode ser votada em todas as categorias.
NOMINEES_BY_CATEGORY = {
    # "Compromisso": ["Alfredo Fernandes", "Carla Alves", "João Ferreira"],
}

# =========================================================
# BASE DE DADOS
# =========================================================
def get_conn() -> sqlite3.Connection:
    # WAL melhora o comportamento quando há leituras e escritas frequentes.
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    """Cria as tabelas necessárias e estados iniciais da app."""
    conn = get_conn()

    # Tabela principal dos votos.
    # UNIQUE(voter_id, category) garante 1 voto por pessoa por categoria.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            voter_id TEXT NOT NULL,
            category TEXT NOT NULL,
            employee TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(voter_id, category)
        )
        """
    )

    # Tabela simples de estado global da app.
    # Aqui guardamos:
    # - categoria atualmente mostrada na apresentação
    # - se os resultados estão visíveis ou escondidos
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT NOT NULL
        )
        """
    )
    conn.commit()

    # Estado inicial da categoria apresentada.
    cursor = conn.execute(
        "SELECT state_value FROM app_state WHERE state_key = ?",
        ("presentation_index",),
    )
    if cursor.fetchone() is None:
        conn.execute(
            "INSERT INTO app_state (state_key, state_value) VALUES (?, ?)",
            ("presentation_index", "0"),
        )

    # Estado inicial: resultados escondidos.
    cursor = conn.execute(
        "SELECT state_value FROM app_state WHERE state_key = ?",
        ("reveal_results",),
    )
    if cursor.fetchone() is None:
        conn.execute(
            "INSERT INTO app_state (state_key, state_value) VALUES (?, ?)",
            ("reveal_results", "0"),
        )

    conn.commit()
    conn.close()


def normalize_voter_id(voter_id: str) -> str:
    # Normaliza o identificador para evitar duplicados com maiúsculas/espaços.
    return voter_id.strip().lower()


def is_valid_email(value: str) -> bool:
    pattern = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
    return re.match(pattern, value.strip()) is not None


def is_allowed_voter_id(voter_id: str) -> bool:
    voter_id = voter_id.strip().lower()

    if not voter_id:
        return False

    # Se ativares a regra, só aceita email do domínio da empresa.
    if REQUIRE_COMPANY_EMAIL:
        return is_valid_email(voter_id) and voter_id.endswith("@" + EMAIL_DOMAIN)

    # Caso contrário, aceita qualquer identificador não vazio.
    return True


def get_nominees(category: str) -> List[str]:
    # Se existir lista própria para a categoria, usa-a.
    # Caso contrário, usa todos os colaboradores.
    nominees = NOMINEES_BY_CATEGORY.get(category, [])
    return nominees if nominees else EMPLOYEES


def save_vote(voter_id: str, category: str, employee: str) -> str:
    """Guarda um voto e devolve:
    - 'ok' se guardou
    - 'duplicate' se essa pessoa já votou nessa categoria
    """
    voter_id = normalize_voter_id(voter_id)
    conn = get_conn()

    try:
        conn.execute(
            "INSERT INTO votes (voter_id, category, employee) VALUES (?, ?, ?)",
            (voter_id, category, employee),
        )
        conn.commit()
        return "ok"
    except sqlite3.IntegrityError:
        return "duplicate"
    finally:
        conn.close()


def load_votes() -> pd.DataFrame:
    conn = get_conn()
    df = pd.read_sql_query(
        "SELECT voter_id, category, employee, created_at FROM votes ORDER BY created_at ASC",
        conn,
    )
    conn.close()
    return df


def delete_all_votes() -> None:
    conn = get_conn()
    conn.execute("DELETE FROM votes")
    conn.commit()
    conn.close()


def get_state(key: str, default: str = "") -> str:
    conn = get_conn()
    cursor = conn.execute(
        "SELECT state_value FROM app_state WHERE state_key = ?",
        (key,),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default


def set_state(key: str, value: str) -> None:
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO app_state (state_key, state_value)
        VALUES (?, ?)
        ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value
        """,
        (key, value),
    )
    conn.commit()
    conn.close()


def get_presentation_index() -> int:
    try:
        idx = int(get_state("presentation_index", "0"))
    except ValueError:
        idx = 0

    return max(0, min(idx, len(CATEGORIES) - 1))


def set_presentation_index(index: int) -> None:
    # Garante que nunca saímos fora da lista de categorias.
    index = max(0, min(index, len(CATEGORIES) - 1))
    set_state("presentation_index", str(index))


def get_reveal_results() -> bool:
    return get_state("reveal_results", "0") == "1"


def set_reveal_results(value: bool) -> None:
    set_state("reveal_results", "1" if value else "0")


def generate_qr_image(url: str) -> BytesIO:
    # Gera QR code em memória, sem criar ficheiros temporários.
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


# =========================================================
# UI - CABEÇALHO
# =========================================================
def show_header() -> None:
    st.markdown(
        """
        <style>
        .header-container {
            background-color: rgba(255, 255, 255, 0.9);
            box-shadow: 0px 0px 5px 0px rgba(0,0,0,0.5);
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
        }
        .header-title {
            color: #174E6D;
            font-family: 'Lato', sans-serif;
            font-size: 2.5rem;
            margin: 0;
        }
        .header-subtitle {
            color: #216390;
            font-family: 'Lato', sans-serif;
            font-size: 1rem;
            margin: 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    
    # Cabeçalho com setas de navegação no topo direito
    st.markdown(
        '''<div style="display:flex;align-items:center;justify-content:space-between;gap:16px;">
            <div style="display:flex;align-items:center;gap:16px;">
                <div style="flex-shrink:0;font-size:2.5rem;line-height:1;">🏆</div>
                <div>
                    <h1 class="header-title" style="margin:0;">CPCecho Awards</h1>
                    <p class="header-subtitle" style="margin:0;">Powered by SmartLabs @ CPCecho 😎</p>
                </div>
            </div>
            <div id="header-nav-arrows"></div>
        </div>''',
        unsafe_allow_html=True,
    )


# =========================================================
# UI - PÁGINA DE VOTO
# =========================================================
def render_vote_page() -> None:
    show_header()
    st.subheader("📱 Votar")
    st.write("Uma pergunta de cada vez. Sem spoilers. Sem batota. Só vibes SmartLabs.")

    voter_id = st.text_input(
        "O teu identificador",
        placeholder="Ex: email ou código de colaborador",
        help=(
            f"Se REQUIRE_COMPANY_EMAIL = True, tens de usar @{EMAIL_DOMAIN}."
            if REQUIRE_COMPANY_EMAIL
            else "Podes usar email ou código de colaborador."
        ),
    )

    if not voter_id:
        st.info("Introduz o teu identificador para começar.")
        return

    if not is_allowed_voter_id(voter_id):
        if REQUIRE_COMPANY_EMAIL:
            st.error(f"Usa o teu email da empresa (@{EMAIL_DOMAIN}).")
        else:
            st.error("Introduz um identificador válido.")
        return

    voter_key = normalize_voter_id(voter_id)
    votes_df = load_votes()

    # Descobre em que categorias esta pessoa já votou.
    already_voted = set()
    if not votes_df.empty:
        already_voted = set(
            votes_df[votes_df["voter_id"] == voter_key]["category"].tolist()
        )

    remaining = [category for category in CATEGORIES if category not in already_voted]
    completed = [category for category in CATEGORIES if category in already_voted]

    c1, c2 = st.columns(2)
    c1.metric("Respondidas", len(completed))
    c2.metric("Por responder", len(remaining))

    # Quando termina tudo, mostramos resumo pessoal.
    if not remaining:
        st.success("Já respondeste a tudo. Missão cumprida. 🏁")
        my_votes = votes_df[votes_df["voter_id"] == voter_key][["category", "employee"]].copy()
        my_votes.columns = ["Categoria", "O teu voto"]
        st.write("### O teu resumo final")
        st.dataframe(my_votes, use_container_width=True, hide_index=True)
        return

    # Mostra apenas a próxima categoria que falta.
    current_category = remaining[0]
    nominees = get_nominees(current_category)

    st.progress(
        len(completed) / len(CATEGORIES),
        text=f"Pergunta {len(completed) + 1} de {len(CATEGORIES)}",
    )

    st.markdown(f"## {current_category}")

    selected_employee = st.selectbox(
        "Escolhe 1 colega",
        nominees,
        index=None,
        placeholder="Seleciona um nome",
        key=f"vote_{current_category}",
    )

    if st.button("Submeter e continuar", use_container_width=True, type="primary"):
        if not selected_employee:
            st.warning("Escolhe um colega antes de submeter.")
            st.stop()

        result = save_vote(voter_id, current_category, selected_employee)

        if result == "ok":
            st.success("Voto registado com sucesso ✅")
            st.rerun()
        else:
            st.error("Este identificador já votou nesta categoria.")


# =========================================================
# UI - QR CODE
# =========================================================
def render_qr_page() -> None:
    show_header()
    st.subheader("🔳 QR Code")
    st.write("Projeta isto no ecrã para o pessoal votar no telemóvel.")

    app_url = st.text_input("URL pública da app", value=APP_URL)

    if not app_url:
        st.warning("Adiciona a URL pública da app.")
        return

    qr_img = generate_qr_image(app_url)

    left, right = st.columns([1, 1])

    with left:
        st.image(qr_img, caption="Scan me. Vote. Be legendary.", width=320)

    with right:
        st.markdown("### CPCecho Awards")
        st.markdown("**Built with mischief by SmartLabs @ CPCecho** 🤖")
        st.markdown("Aponta a câmara, abre o link e começa a votar.")
        st.code(app_url)


# =========================================================
# UI - CÁLCULO DE RESULTADOS
# =========================================================
def build_results_for_category(votes_df: pd.DataFrame, category: str) -> pd.DataFrame:
    # Filtra só os votos da categoria atual.
    category_df = votes_df[votes_df["category"] == category]

    if category_df.empty:
        return pd.DataFrame(columns=["employee", "votes", "percentage"])

    # Conta quantos votos teve cada colaborador.
    results = (
        category_df.groupby("employee")
        .size()
        .reset_index(name="votes")
        .sort_values(["votes", "employee"], ascending=[False, True])
    )

    total_votes = int(results["votes"].sum())

    # Calcula percentagem por colaborador dentro da categoria.
    results["percentage"] = (
        ((results["votes"] / total_votes) * 100).round(1) if total_votes else 0
    )

    return results


# =========================================================
# UI - APRESENTAÇÃO AO VIVO
# =========================================================

def render_live_page() -> None:
    current_index = get_presentation_index()
    current_category = CATEGORIES[current_index]
    reveal = get_reveal_results()
    votes_df = load_votes()

    # ── Fundo CPC azul ─────────────────────────────────────
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"],
        [data-testid="stAppViewContainer"] > div:first-child,
        [data-testid="stMain"],
        .main {
            background-color: #0f2d45 !important;
        }
        [data-testid="stHeader"] {
            background-color: #0f2d45 !important;
        }
        [data-testid="stToolbar"] {
            background-color: #0f2d45 !important;
        }
        /* Header card branco do show_header */
        .header-container {
            background-color: rgba(255,255,255,0.05) !important;
            box-shadow: none !important;
        }
        .header-title {
            color: #FFFFFF !important;
        }
        .header-subtitle {
            color: #8b9ab0 !important;
        }
        /* Sidebar */
        [data-testid="stSidebar"] {
            background-color: #0a1e2e !important;
        }
        [data-testid="stSidebar"] * {
            color: #8b9ab0 !important;
        }
        /* Botões streamlit sobre fundo escuro */
        .stButton > button {
            background-color: rgba(255,255,255,0.12) !important;
            color: #FFFFFF !important;
            border: 1px solid rgba(255,255,255,0.2) !important;
        }
        .stButton > button:hover {
            background-color: rgba(255,255,255,0.22) !important;
        }
        .stButton > button[kind="primary"] {
            background-color: #6BAE8A !important;
            border-color: #6BAE8A !important;
            color: #0f3a52 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ── CSS específico das setas no header e olho na categoria ───────
    st.markdown(
        """
        <style>
        /* Setas + olho no header — 2ª, 3ª e 4ª colunas do 1º bloco horizontal */
        [data-testid="stHorizontalBlock"]:first-of-type [data-testid="stColumn"]:nth-child(2) button,
        [data-testid="stHorizontalBlock"]:first-of-type [data-testid="stColumn"]:nth-child(3) button,
        [data-testid="stHorizontalBlock"]:first-of-type [data-testid="stColumn"]:nth-child(4) button {
            background: transparent !important;
            border: 1.5px solid rgba(255,255,255,0.25) !important;
            color: rgba(255,255,255,0.7) !important;
            border-radius: 50% !important;
            min-height: 34px !important;
            height: 34px !important;
            padding: 0 !important;
            font-size: 0.85rem !important;
            line-height: 1 !important;
            transition: all 0.2s ease !important;
        }
        [data-testid="stHorizontalBlock"]:first-of-type [data-testid="stColumn"]:nth-child(2) button:hover:not(:disabled),
        [data-testid="stHorizontalBlock"]:first-of-type [data-testid="stColumn"]:nth-child(3) button:hover:not(:disabled),
        [data-testid="stHorizontalBlock"]:first-of-type [data-testid="stColumn"]:nth-child(4) button:hover:not(:disabled) {
            background: rgba(255,255,255,0.12) !important;
            border-color: rgba(255,255,255,0.55) !important;
            color: #FFFFFF !important;
        }
        [data-testid="stHorizontalBlock"]:first-of-type [data-testid="stColumn"]:nth-child(2) button:disabled,
        [data-testid="stHorizontalBlock"]:first-of-type [data-testid="stColumn"]:nth-child(3) button:disabled {
            opacity: 0.2 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


    # ── Header com setas + olho na mesma linha ───────────────────────
    hcol_title, hcol_prev, hcol_next, hcol_eye = st.columns([10, 0.55, 0.55, 0.55])
    with hcol_title:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:12px;padding:4px 0 8px 0;">'
            '<span style="font-size:2.2rem;line-height:1;">🏆</span>'
            '<div>'
            '<h1 style="color:#FFFFFF;font-size:clamp(1.6rem,2.5vw,2.4rem);font-weight:800;margin:0;line-height:1.1;">CPCecho Awards</h1>'
            '<p style="color:#8b9ab0;font-size:0.8rem;margin:0;">Powered by SmartLabs @ CPCecho 😎</p>'
            '</div></div>',
            unsafe_allow_html=True,
        )
    with hcol_prev:
        if st.button("◀", key="live_prev_icon", help="Categoria anterior", disabled=current_index == 0, use_container_width=True):
            set_presentation_index(current_index - 1)
            set_reveal_results(False)
            st.rerun()
    with hcol_next:
        if st.button("▶", key="live_next_icon", help="Próxima categoria", disabled=current_index == len(CATEGORIES) - 1, use_container_width=True):
            set_presentation_index(current_index + 1)
            set_reveal_results(False)
            st.rerun()
    with hcol_eye:
        if st.button("👁" if not reveal else "⊘", key="live_eye_icon", help="Mostrar/Esconder resultados", use_container_width=True):
            set_reveal_results(not reveal)
            st.rerun()

    # ── Indicador de progresso ────────────────────────────────────────
    st.markdown(
        f'<div style="text-align:right;color:#a8d4e8;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.1em;padding:2px 0 10px 0;">'
        f'{current_index + 1} / {len(CATEGORIES)}'
        f'</div>',
        unsafe_allow_html=True,
    )

    total_votes = len(votes_df[votes_df["category"] == current_category])
    votes_label = f'{total_votes} voto{"s" if total_votes != 1 else ""}'

    if not reveal:
        card_content = (
            '<div style="text-align:center;padding:32px 0;">'
            '<div style="font-size:3rem;margin-bottom:12px;">🎭</div>'
            '<div style="color:#FFFFFF;font-size:1.3rem;font-weight:700;margin-bottom:8px;">Votação a decorrer</div>'
            '<div style="color:#8b9ab0;font-size:0.95rem;">A audiência está a votar. Carrega no <strong style="color:#6BAE8A;">👁</strong> quando estiverem prontos.</div>'
            '</div>'
        )
    else:
        results = build_results_for_category(votes_df, current_category)
        leader_html = ""
        bars_html = ""
        if not results.empty:
            leader = results.iloc[0]["employee"]
            leader_votes = int(results.iloc[0]["votes"])
            lv_label = "votos" if leader_votes != 1 else "voto"
            leader_html = (
                f'<div style="background:linear-gradient(90deg,#6BAE8A 0%,#85c2a0 100%);border-radius:8px;padding:10px 18px;margin-bottom:20px;display:flex;align-items:center;gap:10px;">'
                f'<span style="font-size:1.3rem;">🏆</span>'
                f'<span style="color:#0e1117;font-weight:700;font-size:1rem;">{leader} &nbsp;·&nbsp; {leader_votes} {lv_label}</span>'
                f'</div>'
            )
            max_v = int(results["votes"].max())
            for i, row in results.iterrows():
                name = row["employee"]
                votes = int(row["votes"])
                pct = float(row["percentage"])
                width = (votes / max_v * 100) if max_v > 0 else 0
                is_lead = i == results.index[0]
                bar_color = "#6BAE8A" if is_lead else "#6AADD5"
                fw = "font-weight:700;font-size:1.1rem;" if is_lead else "font-size:1rem;"
                trophy = " 🏆" if is_lead else ""
                mw = 2 if votes > 0 else 0
                bars_html += (
                    f'<div style="margin-bottom:16px;">'
                    f'<div style="color:#FFFFFF;{fw}margin-bottom:5px;">{name}{trophy}</div>'
                    f'<div style="display:flex;align-items:center;gap:10px;">'
                    f'<div style="flex:1;background:#0f2d45;border-radius:5px;height:26px;overflow:hidden;">'
                    f'<div style="width:{width}%;height:100%;background:{bar_color};border-radius:5px;min-width:{mw}px;"></div>'
                    f'</div>'
                    f'<div style="color:#FFFFFF;font-size:1rem;font-weight:600;min-width:46px;text-align:right;">{pct:.0f}%</div>'
                    f'</div>'
                    f'</div>'
                )
        no_votes_html = '<div style="color:#8b9ab0;text-align:center;padding:16px;">Ainda não há votos.</div>' if results.empty else ""
        card_content = leader_html + no_votes_html + bars_html

    # ── Card: título + votos + conteúdo ──────────────────────────────
    st.markdown(
        f'<div style="background:#071828;border-radius:16px;padding:28px 32px;">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">'
        f'<h2 style="color:#FFFFFF;font-size:clamp(1.4rem,2.5vw,2rem);font-weight:800;margin:0;">{current_category}</h2>'
        f'<span style="color:#6BAE8A;font-size:1.4rem;font-weight:700;">{votes_label}</span>'
        f'</div>'
        f'<hr style="border-color:rgba(255,255,255,0.15);margin:12px 0 20px 0;">'
        f'{card_content}'
        f'</div>',
        unsafe_allow_html=True,
    )


# =========================================================
# UI - RESUMO FINAL
# =========================================================
def render_final_summary_page() -> None:
    show_header()
    st.subheader("📊 Resumo final")
    st.write("Aqui tens o ranking completo de todas as categorias.")

    votes_df = load_votes()

    if votes_df.empty:
        st.info("Ainda não há votos submetidos.")
        return

    s1, s2 = st.columns(2)
    s1.metric("Total de votos", len(votes_df))
    s2.metric("Votantes únicos", votes_df["voter_id"].nunique())

    for idx, category in enumerate(CATEGORIES, start=1):
        st.markdown(f"## {idx}. {category}")

        results = build_results_for_category(votes_df, category)

        if results.empty:
            st.caption("Ainda sem votos.")
            st.divider()
            continue

        winner = results.iloc[0]["employee"]
        winner_votes = int(results.iloc[0]["votes"])

        st.success(f"Vencedor atual: {winner} com {winner_votes} votos")

        display_df = results.rename(
            columns={"employee": "Colaborador", "votes": "Votos", "percentage": "%"}
        )

        if not SHOW_PERCENTAGES:
            display_df = display_df[["Colaborador", "Votos"]]

        st.dataframe(display_df, use_container_width=True, hide_index=True)
        st.divider()


# =========================================================
# UI - ADMIN
# =========================================================
def render_admin_page() -> None:
    show_header()
    st.subheader("🛠️ Admin")

    code = st.text_input("Código admin", type="password")
    if code != ADMIN_CODE:
        st.info("Introduz o código de admin.")
        return

    st.success("Acesso admin autorizado.")

    current_index = get_presentation_index()
    current_category = CATEGORIES[current_index]
    reveal = get_reveal_results()

    st.write("### Controlo da apresentação")
    st.write(f"Categoria atual: **{current_index + 1}. {current_category}**")
    st.write(f"Resultados visíveis: **{'Sim' if reveal else 'Não'}**")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if st.button("Reset categoria 1", use_container_width=True):
            set_presentation_index(0)
            set_reveal_results(False)
            st.rerun()

    with col2:
        if st.button("Back", use_container_width=True, disabled=current_index == 0):
            set_presentation_index(current_index - 1)
            set_reveal_results(False)
            st.rerun()

    with col3:
        if st.button("Next", use_container_width=True, disabled=current_index == len(CATEGORIES) - 1):
            set_presentation_index(current_index + 1)
            set_reveal_results(False)
            st.rerun()

    with col4:
        if not reveal:
            if st.button("Ver", use_container_width=True):
                set_reveal_results(True)
                st.rerun()
        else:
            if st.button("Esconder", use_container_width=True):
                set_reveal_results(False)
                st.rerun()

    st.write("### Exportar votos")
    votes_df = load_votes()

    if votes_df.empty:
        st.caption("Ainda não existem votos.")
    else:
        csv_data = votes_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="Download CSV",
            data=csv_data,
            file_name="cpcecho_awards_votes.csv",
            mime="text/csv",
        )
        st.dataframe(votes_df, use_container_width=True, hide_index=True)

    st.write("### Danger zone ☠️")
    confirm_reset = st.checkbox("Confirmo que quero apagar todos os votos")

    if st.button("Apagar todos os votos", type="secondary", use_container_width=True):
        if confirm_reset:
            delete_all_votes()
            set_presentation_index(0)
            set_reveal_results(False)
            st.warning("Todos os votos foram apagados.")
            st.rerun()
        else:
            st.error("Tens de confirmar primeiro.")


# =========================================================
# MAIN
# =========================================================
init_db()

# Pequenos ajustes visuais.
st.markdown(
    """
    <style>
        .block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
        div[data-testid="stMetricValue"] {font-size: 2rem;}
        
        /* Adaptação ao design do site CPCecho */
        :root {
            --primary-color: #174E6D;
            --secondary-color: #216390;
            --accent-color: #FF4357;
            --background-color: #FFFFFF;
            --text-color: #216390;
            --font-family: 'Lato', sans-serif;
        }
        
        body {
            font-family: var(--font-family);
            color: var(--text-color);
        }
        
        .stButton button {
            background-color: var(--primary-color);
            color: white;
            border-radius: 100px;
            border: none;
            font-family: var(--font-family);
        }
        
        .stButton button:hover {
            background-color: var(--secondary-color);
        }
        
        .stTextInput input, .stSelectbox select, .stTextArea textarea {
            border-radius: 8px;
            border: 1px solid #ddd;
        }
        
        .stSidebar {
            background-color: #f9f9f9;
        }
        
        h1, h2, h3, h4, h5, h6 {
            color: var(--primary-color);
            font-family: var(--font-family);
        }
        
        .stSuccess, .stInfo, .stWarning, .stError {
            border-radius: 8px;
        }
        
        .stMetric {
            background-color: #f0f8ff;
            border-radius: 8px;
            padding: 10px;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

if mode == "vote":
    page = "Vote"
else:
    page = st.sidebar.radio(
        "Navigation",
        ["Vote", "QR Code", "Live Presentation", "Final Summary", "Admin"],
        index=0,
    )

st.sidebar.markdown("---")
st.sidebar.write("**CPCecho Awards**")
st.sidebar.caption("Built by SmartLabs @ CPCecho")
st.sidebar.caption(f"Categorias: {len(CATEGORIES)}")
st.sidebar.caption(f"Colaboradores: {len(EMPLOYEES)}")


if page == "Vote":
    render_vote_page()
elif page == "QR Code":
    render_qr_page()
elif page == "Live Presentation":
    render_live_page()
elif page == "Final Summary":
    render_final_summary_page()
else:
    render_admin_page()