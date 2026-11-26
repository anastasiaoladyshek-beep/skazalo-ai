import streamlit as st
import json, os, time, re, random
from openai import OpenAI

# ---------- настройки ----------
st.set_page_config(
    page_title="Сказало.AI",
    page_icon="🎭",
    layout="centered",
    initial_sidebar_state="expanded",
)

THEME_CSS = """
<style>
    .stApp { background-color: #0e0e12; color: #e8e0d0; }
    [data-testid="stSidebar"] { background-color: #1a1a24; }
    .stChatMessage { border-radius: 12px; }
</style>
"""
st.markdown(THEME_CSS, unsafe_allow_html=True)

DEFAULT_CREDITS = 500
COST_PER_TURN = 10

PROMPTS_DIR = "characters"
os.makedirs(PROMPTS_DIR, exist_ok=True)

# ---------- Универсальный доступ к секретам (env или st.secrets) ----------
def get_secret(key: str, default: str = "") -> str:
    val = os.getenv(key)
    if val:
        return val
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

# ---------- World Book ----------
WORLDBOOK = {
    "таверна": "Таверна «Пьяный грифон» — место, где встречаются все. Здесь пахнет элем и старыми историями.",
    "король": "Король Артур болен, наследника нет. При дворе растёт напряжение.",
    "монета": "Старая монета с профилем неизвестного короля. Ходит легенда, что она приносит удачу.",
    "дождь": "В этом городе дождь идёт почти постоянно. Улицы скользкие, фонари тусклые.",
}

def get_worldbook_context(text: str) -> str:
    hits = [v for k, v in WORLDBOOK.items() if k.lower() in text.lower()]
    return "\n".join(hits)

# ---------- Безопасные имена файлов ----------
def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\s\-]", "", name, flags=re.UNICODE).strip()
    return name[:50]

# ---------- Стартовые персонажи ----------
DEFAULT_CHARACTERS = {
    "Загадочный странник": {
        "emoji": "🌑",
        "system": "Ты — загадочный странник в таверне фэнтезийного города. Говоришь загадочно, коротко, иногда задаёшь встречные вопросы. Никогда не выходишь из роли.",
        "greeting": "*Странник поднимает взгляд из-под капюшона.* Итак, ты всё-таки пришёл. Что тебе нужно, путник?",
        "world": "Фэнтезийный город Элдервуд, таверна «Пьяный грифон». Магия здесь редкость, но слухи о ней ходят.",
        "lore": ["Странник никогда не называет своего имени", "В таверне есть скрытая дверь за барной стойкой"],
    },
    "Корабельный ИИ «Вектор»": {
        "emoji": "🛰️",
        "system": "Ты — бортовой ИИ исследовательского корабля «Вектор». Говоришь спокойно, технично, сухим юмором. Обращаешься к пользователю «Командир».",
        "greeting": "Командир, все системы в норме. Мы на орбите неизвестной планеты. С чего начнём разведку?",
        "world": "Корабль «Вектор» — исследовательское судно дальнего действия. На борту 12 членов экипажа, включая Командира.",
        "lore": ["На планете обнаружены следы древней цивилизации", "Связь с базой нестабильна из-за магнитных аномалий"],
    },
    "Детектив Морроу": {
        "emoji": "🕵️",
        "system": "Ты — ночной детектив Морроу из мрачного нуарного города 1940-х. Говоришь короткими фразами, куришь (в речи), задаёшь неудобные вопросы.",
        "greeting": "Дождь опять идёт третий день. Заходи, только не нанеси грязь на ковёр. Рассказывай, что стряслось.",
        "world": "Город Нью-Хейвен, 1947 год. Коррупция, джаз-клубы, дождь. Полиция закрывает глаза на многое.",
        "lore": ["В участке есть продажный коп по кличке «Тень»", "Клиенты никогда не говорят всей правды"],
    },
}

for name, ch in DEFAULT_CHARACTERS.items():
    path = os.path.join(PROMPTS_DIR, f"{name}.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ch, f, ensure_ascii=False, indent=2)

def load_characters():
    chars = {}
    for fn in os.listdir(PROMPTS_DIR):
        if fn.endswith(".json"):
            base = fn[:-5]
            if safe_filename(base) != base:
                continue
            with open(os.path.join(PROMPTS_DIR, fn), encoding="utf-8") as f:
                chars[base] = json.load(f)
    return chars

def save_character(name, data):
    safe = safe_filename(name)
    if not safe:
        raise ValueError("Недопустимое имя персонажа")
    with open(os.path.join(PROMPTS_DIR, f"{safe}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---------- Состояние сессии ----------
if "credits" not in st.session_state:
    st.session_state.credits = DEFAULT_CREDITS
if "active_char" not in st.session_state:
    st.session_state.active_char = None
if "turn_count" not in st.session_state:
    st.session_state.turn_count = 0
if "last_bonus" not in st.session_state:
    st.session_state.last_bonus = 0

if "story_tree" not in st.session_state:
    st.session_state.story_tree = {
        "current_node_id": "root",
        "nodes": {"root": {"parent": None, "role": "assistant", "content": "", "children": []}},
    }

def reset_story(greeting: str):
    st.session_state.story_tree = {
        "current_node_id": "root",
        "nodes": {"root": {"parent": None, "role": "assistant", "content": greeting, "children": []}},
    }
    st.session_state.turn_count = 0

def get_current_history():
    nodes = st.session_state.story_tree["nodes"]
    path, nid = [], st.session_state.story_tree["current_node_id"]
    while nid:
        node = nodes[nid]
        path.append({"role": node["role"], "content": node["content"]})
        nid = node["parent"]
    return list(reversed(path))

def add_message(role: str, content: str):
    tree = st.session_state.story_tree
    parent_id = tree["current_node_id"]
    new_id = f"msg_{int(time.time() * 1000)}"
    tree["nodes"][new_id] = {"parent": parent_id, "role": role, "content": content, "children": []}
    tree["nodes"][parent_id]["children"].append(new_id)
    tree["current_node_id"] = new_id

def rollback():
    tree = st.session_state.story_tree
    current = tree["nodes"][tree["current_node_id"]]
    if current["parent"] is None:
        return False
    tree["current_node_id"] = current["parent"]
    return True

# ---------- Сайдбар ----------
with st.sidebar:
    st.title("🎭 Сказало.AI")
    chars = load_characters()
    if not chars:
        st.error("Нет доступных персонажей.")
        st.stop()

    choice = st.selectbox(
        "Персонаж",
        list(chars.keys()),
        index=list(chars.keys()).index(st.session_state.active_char)
        if st.session_state.active_char in chars else 0,
    )

    if choice != st.session_state.active_char:
        st.session_state.active_char = choice
        reset_story(chars[choice]["greeting"])
        st.rerun()

    st.metric("Кредиты", st.session_state.credits)
    st.caption(f"Один ход стоит {COST_PER_TURN} кредитов")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("➕ Кредиты", use_container_width=True):
            now = time.time()
            if now - st.session_state.last_bonus > 60:
                st.session_state.credits += 100
                st.session_state.last_bonus = now
                st.toast("+100 кредитов!")
            else:
                st.toast("Ещё рано, попробуй позже")
    with col2:
        if st.button("🔄 Новая сцена", use_container_width=True):
            reset_story(chars[st.session_state.active_char]["greeting"])
            st.rerun()

    if st.button("↩ Откатиться на ход назад", use_container_width=True):
        if rollback():
            st.rerun()
        else:
            st.toast("Это уже начало сцены")

    with st.expander("🌳 Дерево диалога"):
        nodes = st.session_state.story_tree["nodes"]
        cur = st.session_state.story_tree["current_node_id"]
        lines = []
        for nid, node in nodes.items():
            marker = "👉" if nid == cur else "  "
            role = "🧑" if node["role"] == "user" else "🎭"
            snippet = node["content"][:60].replace("\n", " ")
            lines.append(f"{marker} {role} {snippet}")
        st.code("\n".join(lines) or "(пусто)", language="text")

    st.divider()
    st.subheader("Создать персонажа")
    with st.form("create_character_form", clear_on_submit=False):
        new_name = st.text_input("Имя (Name) *")
        new_emoji = st.text_input("Эмодзи", "✨", max_chars=2)
        new_personality = st.text_area("Личность (Personality)", height=100)
        new_scenario = st.text_area("Сценарий (Scenario)", height=80)
        new_first_mes = st.text_area("Первое сообщение (First Message)", height=80)
        new_creator_comment = st.text_area("Комментарий создателя", height=60)
        new_world = st.text_area("Мир / локация", height=60)
        new_lore = st.text_area("Факты о мире (по одному на строку)", height=60)
        submitted = st.form_submit_button("Сохранить персонажа", use_container_width=True)

        if submitted:
            if not new_name or not safe_filename(new_name):
                st.error("Введи корректное имя персонажа.")
            else:
                try:
                    save_character(new_name, {
                        "emoji": new_emoji,
                        "personality": new_personality,
                        "scenario": new_scenario,
                        "first_mes": new_first_mes,
                        "creatorcomment": new_creator_comment,
                        "system": new_personality or new_scenario or "Ты — персонаж ролевой игры.",
                        "greeting": new_first_mes or "Привет.",
                        "world": new_world,
                        "lore": [ln.strip() for ln in new_lore.splitlines() if ln.strip()],
                    })
                    st.success(f"Персонаж «{new_name}» создан!")
                except ValueError as e:
                    st.error(str(e))

# ---------- Основной экран ----------
if not st.session_state.active_char:
    st.info("Выбери персонажа слева →")
    st.stop()

ch = chars[st.session_state.active_char]
st.title(f'{ch.get("emoji", "🎭")} {st.session_state.active_char}')
st.caption(f'Кредитов: {st.session_state.credits} · Ходов в сцене: {st.session_state.turn_count}')

for m in get_current_history():
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

# ---------- Случайное событие ----------
EVENTS = [
    "В дверь таверны стучат. Громко. Очень настойчиво.",
    "Свет свечей гаснет на секунду, и когда он возвращается, на столе лежит записка.",
    "Странник достаёт из кармана старую монету и подбрасывает её.",
    "С улицы доносится крик, и все в комнате замирают.",
    "На столе появляется карта с отметкой в месте, которого нет ни в одном путеводителе.",
]

if st.button("🎲 Случайное событие"):
    add_message("assistant", random.choice(EVENTS))
    st.rerun()

# ---------- Ход ----------
user_input = st.chat_input("Твой ход...")
if user_input:
    if len(user_input) > 2000:
        st.warning("Слишком длинное сообщение (макс. 2000 символов).")
        st.stop()

    if st.session_state.credits < COST_PER_TURN:
        st.warning("Кредиты кончились! Заработай их в сайдбаре 🙂")
        st.stop()

    api_key = get_secret("OPENAI_API_KEY")
    base_url = get_secret("OPENAI_BASE_URL") or None
    model_name = get_secret("MODEL_NAME", "gpt-4o-mini")

    if not api_key:
        st.error("Нет API-ключа. Добавь OPENAI_API_KEY в Secrets на Streamlit Cloud.")
        st.stop()

    system_parts = [ch.get("system", "")]
    if ch.get("personality"):
        system_parts.append(f"Личность: {ch['personality']}")
    if ch.get("scenario"):
        system_parts.append(f"Сценарий: {ch['scenario']}")
    if ch.get("world"):
        system_parts.append(f"Мир: {ch['world']}")
    if ch.get("lore"):
        system_parts.append("Факты о мире:\n" + "\n".join(f"- {x}" for x in ch["lore"]))
    wb = get_worldbook_context(user_input)
    if wb:
        system_parts.append("Контекст из мировой книги:\n" + wb)

    system_prompt = "\n\n".join(p for p in system_parts if p)
    history_for_api = [{"role": "system", "content": system_prompt}, *get_current_history()[-30:]]

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        with st.spinner("Сцена пишется..."):
            resp = client.chat.completions.create(
                model=model_name,
                messages=history_for_api,
                temperature=0.9,
                max_tokens=600,
            )
        reply = resp.choices[0].message.content
    except Exception as e:
        st.error(f"Ошибка API: {e}")
        st.stop()

    st.session_state.credits -= COST_PER_TURN
    st.session_state.turn_count += 1
    add_message("user", user_input)
    add_message("assistant", reply)
    st.rerun()
