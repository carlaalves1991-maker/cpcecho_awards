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
    
    col1, col2 = st.columns([1, 4])
    
    with col1:
        logo_path = Path("logo.png")
        if logo_path.exists():
            # Embed SVG directly to avoid deployment issues
            svg_content = '''<svg id="Camada_1" data-name="Camada 1" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 40"><defs><style>.cls-1{fill:#216390;}.cls-2{fill:#606060;}</style></defs><circle class="cls-1" cx="181.64" cy="15.94" r="4.06"/><path class="cls-2" d="M195,16.46v.06c0,7.05-6,12.78-13.36,12.78s-13.37-5.73-13.37-12.78v-.06h4.22v.06a9.15,9.15,0,0,0,18.29,0v-.06Z"/><path class="cls-1" d="M195,15h-4.22c0-4.68-4.12-8.47-9.14-8.47s-9.11,3.79-9.15,8.47h-4.22c0-6.85,6-12.41,13.37-12.41S195,8.13,195,15Z"/><rect class="cls-1" x="102.15" y="5.39" width="17.11" height="4.18" transform="translate(-0.01 0.21) rotate(-0.11)"/><path class="cls-2" d="M106.46,24V18.85l12.37,0v-4l-16.66,0,0,13.42,17.11,0V24Z"/><path class="cls-2" d="M142.75,25.37a12,12,0,0,1-8.68,3.4C125.58,28.77,122,22.92,122,17s3.86-12,12.12-12a11.79,11.79,0,0,1,8.39,3.5l-2.88,2.77a7.82,7.82,0,0,0-5.51-2.15c-5.52,0-7.91,4.11-7.87,7.93s2.22,7.74,7.87,7.74a8.41,8.41,0,0,0,5.74-2.32Z"/><path class="cls-2" d="M160.74,28.28V19H149.51v9.24H145.2V5.42h4.31v9.64h11.23V5.42H165V28.28Z"/><path class="cls-1" d="M5,22.43c0-.34.06-.69.11-1,.24-1.6.46-3.19.75-4.78A30.08,30.08,0,0,1,7.3,11.19a11.21,11.21,0,0,1,2-3.53,7.42,7.42,0,0,1,4.3-2.43A15.48,15.48,0,0,1,16.34,5c4.79,0,9.59,0,14.38,0A2.15,2.15,0,0,1,33,6.87,4.08,4.08,0,0,1,30.71,11a2.53,2.53,0,0,1-.86.17h-11a4.26,4.26,0,0,0-4.5,3.21,30.69,30.69,0,0,0-.84,3.56,13.16,13.16,0,0,0-.23,2.34,2.23,2.23,0,0,0,2,2.34,5.69,5.69,0,0,0,.83.06H26.94a2.16,2.16,0,0,1,2.35,2,4,4,0,0,1-2.77,4.21,1.47,1.47,0,0,1-.36,0H11.08a8.22,8.22,0,0,1-3.21-.58,4.33,4.33,0,0,1-2.69-3.46C5.11,24.4,5.06,24,5,23.54Z"/><path class="cls-1" d="M50.11,4.94c2.71,0,5.42,0,8.14,0a9.16,9.16,0,0,1,3,.44A4.35,4.35,0,0,1,64.3,8.91a12.68,12.68,0,0,1,.07,3.74A55.45,55.45,0,0,1,63,20.24a17,17,0,0,1-1.85,4.67,7.8,7.8,0,0,1-5.83,3.8,17.32,17.32,0,0,1-2.27.16c-3.45,0-6.9,0-10.34,0-.27,0-.37.07-.42.34-.39,2-.8,4-1.19,6a2,2,0,0,1-1,1.34,5.42,5.42,0,0,1-2.28.77,7.18,7.18,0,0,1-3.06-.17,4.71,4.71,0,0,1-1.22-.58,1.19,1.19,0,0,1-.48-1.33c.65-3.19,1.32-6.38,2-9.57l2.37-11.57c.45-2.21.9-4.43,1.37-6.64a3.15,3.15,0,0,1,3.16-2.54c2.72,0,5.44,0,8.17,0ZM47.59,22.67h0c1.19,0,2.38,0,3.56,0a4,4,0,0,0,2.3-.75,4.63,4.63,0,0,0,1.72-2.53c.3-1,.52-2.09.74-3.14a9.48,9.48,0,0,0,.29-2.91,2.17,2.17,0,0,0-1.58-2,4.07,4.07,0,0,0-1.15-.19c-2.3,0-4.6,0-6.91,0-.46,0-.56.08-.66.54q-.87,4-1.72,7.92c-.19.83-.36,1.66-.54,2.5-.09.46,0,.55.44.55Z"/><path class="cls-1" d="M81.51,28.88c-2.55,0-5.1,0-7.64,0a7.73,7.73,0,0,1-3.17-.65,4.28,4.28,0,0,1-2.52-3.34,11.93,11.93,0,0,1-.07-3.63,53.45,53.45,0,0,1,1.46-7.83,16.07,16.07,0,0,1,1.91-4.66,7.78,7.78,0,0,1,5.81-3.65A20.51,20.51,0,0,1,79.89,5c4.6,0,9.2,0,13.8,0a2.06,2.06,0,0,1,2.22,1.72,3,3,0,0,1-.07,1.84,4.49,4.49,0,0,1-1.73,2.25,2.37,2.37,0,0,1-1.34.4c-3.61,0-7.23,0-10.84,0a4.33,4.33,0,0,0-4.62,3.36,30.48,30.48,0,0,0-.84,3.67,10,10,0,0,0-.17,2.18,2.19,2.19,0,0,0,2,2.25,5.69,5.69,0,0,0,.83.06H89.9a2.19,2.19,0,0,1,2.38,2,4,4,0,0,1-2.78,4.17,1.9,1.9,0,0,1-.41,0Z"/></svg>'''
            st.markdown(f'<div style="width: 110px;">{svg_content}</div>', unsafe_allow_html=True)
        else:
            st.markdown("# 🏆")
    
    with col2:
        st.markdown('<h1 class="header-title">CPCecho Awards</h1>', unsafe_allow_html=True)
        st.markdown('<p class="header-subtitle">Powered by SmartLabs @ CPCecho 😎</p>', unsafe_allow_html=True)


# =========================================================
# UI - PÁGINA DE VOTO
# =========================================================
def render_vote_page() -> None:
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
    st.subheader("🎤 Apresentação")
    st.write("Mostra apenas 1 categoria de cada vez. Os resultados ficam escondidos até carregares em 'Ver resultados'.")

    current_index = get_presentation_index()
    current_category = CATEGORIES[current_index]
    reveal = get_reveal_results()
    votes_df = load_votes()

    # Botões de navegação entre categorias.
    a, b, c, d = st.columns([1, 1, 1, 2])

    with a:
        if st.button("⬅ Previous", use_container_width=True, disabled=current_index == 0):
            set_presentation_index(current_index - 1)
            set_reveal_results(False)  # sempre que mudas de pergunta, esconde resultados
            st.rerun()

    with b:
        if st.button("Next ➡", use_container_width=True, disabled=current_index == len(CATEGORIES) - 1):
            set_presentation_index(current_index + 1)
            set_reveal_results(False)  # esconde resultados na próxima pergunta
            st.rerun()

    with c:
        if not reveal:
            if st.button("👀 Ver resultados", use_container_width=True):
                set_reveal_results(True)
                st.rerun()
        else:
            if st.button("🙈 Esconder resultados", use_container_width=True):
                set_reveal_results(False)
                st.rerun()

    with d:
        st.info(f"A mostrar categoria {current_index + 1} de {len(CATEGORIES)}")

    st.markdown(f"# {current_category}")

    # Enquanto não clicares em "Ver resultados", não mostra nada da votação.
    if not reveal:
        st.warning("Resultados escondidos. Carrega em **Ver resultados** quando quiseres revelar.")
        st.markdown(
            """
            ### 🎭 Modo suspense ativado
            A audiência já pode estar a votar, mas os resultados continuam secretos.
            """
        )
        return

    total_votes = len(votes_df[votes_df["category"] == current_category])
    total_unique_voters = votes_df["voter_id"].nunique() if not votes_df.empty else 0

    d1, d2 = st.columns(2)
    d1.metric("Votos nesta categoria", total_votes)
    d2.metric("Votantes únicos", total_unique_voters)

    results = build_results_for_category(votes_df, current_category)

    if results.empty:
        st.warning("Ainda não há votos para esta categoria.")
        return

    leader = results.iloc[0]["employee"]
    leader_votes = int(results.iloc[0]["votes"])

    st.success(f"Líder atual: {leader} com {leader_votes} votos 🏆")

    chart_df = results.set_index("employee")[["votes"]]
    st.bar_chart(chart_df)

    display_df = results.rename(
        columns={"employee": "Colaborador", "votes": "Votos", "percentage": "%"}
    )

    if not SHOW_PERCENTAGES:
        display_df = display_df[["Colaborador", "Votos"]]

    st.dataframe(display_df, use_container_width=True, hide_index=True)


# =========================================================
# UI - RESUMO FINAL
# =========================================================
def render_final_summary_page() -> None:
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
show_header()

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