"""AI 智能伴侣页面原型。运行：python -m streamlit run app.py"""
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from openai import OpenAI, OpenAIError
from datetime import datetime
from html import escape

import streamlit as st
from uuid import uuid4

st.set_page_config(page_title="心伴 · AI 智能伴侣", page_icon="💜", layout="wide")

st.markdown("""
<style>
    .stApp {background: #f7f6fc; color: #302c45;}
    .block-container {padding-top: 2rem; padding-bottom: 2rem; max-width: 1500px;}
    [data-testid="stSidebar"] {background: #eeebf8;}
    [data-testid="stVerticalBlockBorderWrapper"] {border-radius: 20px;}
    [data-testid="stChatMessage"] {background: #f1eefb; border-radius: 16px;}
    .brand {font-size: 28px; font-weight: 800; color: #6f55bc;}
    .eyebrow {font-size: 12px; letter-spacing: 2px; color: #9685ba; margin-bottom: 8px;}
    .hero {padding: 24px 28px; border-radius: 22px; background: linear-gradient(115deg,#e9e1ff,#fcebf4); margin-bottom: 24px;}
    .hero h1 {font-size: 28px; margin: 0 0 8px; color: #453766;}
    .hero p {margin: 0; color: #766987;}
    .portrait {text-align: center; padding: 28px 10px 16px;}
    .avatar {font-size: 68px; background: #eee5ff; width: 126px; height: 126px; border-radius: 50%; margin: auto; display: flex; align-items: center; justify-content: center;}
    .portrait h2 {margin: 12px 0 3px; font-size: 24px;}
    .muted {color: #8b849a; font-size: 14px;}
    .tag {display: inline-block; padding: 5px 10px; border-radius: 20px; background: #f0eaff; color: #8063b8; font-size: 12px; margin: 4px 2px;}
    .note {padding: 16px; border-radius: 14px; background: #fbf5e9; color: #89714d; line-height: 1.8;}
    div.stButton > button {border-radius: 12px;}
    [data-testid="stMetricValue"] {color: #8060bd;}
</style>
""", unsafe_allow_html=True)


# 固定在本文件旁边，避免终端工作目录变化时找错数据库。
DB_PATH = Path(__file__).resolve().parent / "data" / "companion.db"


@contextmanager
def database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_database():
    with database() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(conversation_id, position),
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                    ON DELETE CASCADE
            );
        """)


def store_chat(chat_id, title, messages):
    # 整轮对话在一个事务中保存，避免只保存一半。
    with database() as conn:
        conn.execute("""
            INSERT INTO conversations(id, title) VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET title = excluded.title
        """, (chat_id, title))
        conn.executemany("""
            INSERT INTO messages(conversation_id, position, role, content)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(conversation_id, position) DO UPDATE SET
                role = excluded.role, content = excluded.content
        """, [(chat_id, i, m["role"], m["content"]) for i, m in enumerate(messages)])
        conn.execute("DELETE FROM messages WHERE conversation_id = ? AND position >= ?",
                     (chat_id, len(messages)))


def load_chats():
    with database() as conn:
        chats = {row[0]: {"title": row[1], "messages": []}
                 for row in conn.execute("SELECT id, title FROM conversations ORDER BY rowid")}
        for chat_id, role, content in conn.execute(
                "SELECT conversation_id, role, content FROM messages ORDER BY conversation_id, position"):
            chats[chat_id]["messages"].append({"role": role, "content": content})
    return chats


try:
    init_database()
except (sqlite3.Error, OSError):
    st.error("数据库初始化失败，请检查数据库文件是否被占用或表结构是否匹配。")
    st.stop()


def initial_messages():
    return [{"role": "assistant", "content": "嗨，我在这里。今天有什么想和我分享的吗？开心的小事、烦恼，或者只是随便聊聊都可以。💜"}]


if "messages" not in st.session_state:
    st.session_state.messages = initial_messages()
if "mood_records" not in st.session_state:
    st.session_state.mood_records = []

# 首次加载时读取数据库；也将升级前仍在内存中的历史记录迁移进去。
if "sqlite_loaded" not in st.session_state:
    try:
        for old_id, old_chat in st.session_state.get("conversations", {}).items():
            store_chat(old_id, old_chat["title"], old_chat["messages"])
        chats = load_chats()
        if not chats:
            chat_id = uuid4().hex
            store_chat(chat_id, "新对话", st.session_state.messages)
            chats = load_chats()
        st.session_state.conversations = chats
        current = st.session_state.get("current_chat")
        st.session_state.current_chat = current if current in chats else next(reversed(chats))
        st.session_state.messages = [dict(m) for m in chats[st.session_state.current_chat]["messages"]]
        st.session_state.sqlite_loaded = True
    except (sqlite3.Error, OSError):
        st.error("读取历史记录失败，请检查数据库是否被其他程序锁定。")
        st.stop()

def save_current_chat():
    messages = [dict(m) for m in st.session_state.messages]

    # 用第一条用户消息作为标题
    question = next(
        (m["content"] for m in messages if m["role"] == "user"),
        "",
    )
    title = " ".join(question.split())

    title = (title[:18] + "…" if len(title) > 18 else title) or "新对话"
    try:
        store_chat(st.session_state.current_chat, title, messages)
    except (sqlite3.Error, OSError):
        st.error("聊天记录保存失败，请关闭数据库编辑事务后重试；当前消息仍保留在页面中。")
        st.stop()
    st.session_state.conversations[st.session_state.current_chat] = {
        "title": title,
        "messages": messages,
    }

def new_chat():
    save_current_chat()

    st.session_state.current_chat = uuid4().hex
    st.session_state.messages = initial_messages()

    save_current_chat()
    st.session_state.space_page = "💬 陪伴聊天"

def switch_chat(chat_id):
    save_current_chat()

    st.session_state.current_chat = chat_id
    st.session_state.messages = [
        dict(m)
        for m in st.session_state.conversations[chat_id]["messages"]
    ]
    st.session_state.space_page = "💬 陪伴聊天"

# 页面重新运行时，自动保存当前对话
save_current_chat()


def send_message(message):
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

    if not api_key:
        st.error("未读取到密钥，请检查环境变量并重启 PyCharm。")
        st.stop()

    messages = [
        *st.session_state.messages,
        {"role": "user", "content": message},
    ]

    system_prompt = (
        f"你是名叫{companion_name or '小暖'}的 AI 陪伴伙伴，"
        f"交流风格是{personality}。"
        "请用自然、温暖的中文交流，结合上下文回答，"
        "认真倾听用户，避免机械重复，如实表明自己的 AI 身份。"
    )

    try:
        with st.spinner("正在回复……"):
            with OpenAI(
                    api_key=api_key,
                    base_url="https://api.deepseek.com",
                    timeout=60.0,
                    max_retries=0,
            ) as client:
                response = client.chat.completions.create(
                    model="deepseek-flash",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        *messages,
                    ],
                    max_tokens=2048,
                    stream=False,
                    extra_body={"thinking": {"type": "disabled"}},
                )

            reply = response.choices[0].message.content

    except OpenAIError:
        st.error("调用失败，请检查 DeepSeek 密钥、账户余额或网络连接。")
        st.stop()

    if not reply or not reply.strip():
        st.error("模型未返回有效回复，请重新发送。")
        st.stop()

    st.session_state.messages = [
        *messages,
        {"role": "assistant", "content": reply},
    ]
    save_current_chat()


with st.sidebar:
    st.markdown('<div class="brand">💜 心伴</div>', unsafe_allow_html=True)
    st.caption("让每一天，都有温柔回应")
    st.divider()
    page = st.radio("我的空间", ["💬 陪伴聊天", "📔 心情日记", "⚙️ 伴侣设置"], label_visibility="collapsed", key="space_page")
    st.divider()
    companion_name = st.text_input("伴侣昵称", value="小暖", max_chars=20)
    personality = st.selectbox("陪伴风格", ["温柔倾听", "活泼开朗", "理性耐心"])
    st.caption("昵称和陪伴风格会应用到 AI 回复。")
    st.button(
        "＋ 开始新的对话",
        width="stretch",
        on_click=new_chat,
    )

    st.caption("聊天记录已保存至本机 SQLite。此版本供个人使用，所有访问者共用历史记录。")
    with st.expander("数据库文件位置"):
        st.code(str(DB_PATH), language=None)
    st.markdown("#### 历史对话")

    # 最新创建的对话显示在最上面
    for chat_id, chat in reversed(
        list(st.session_state.conversations.items())
    ):
        st.button(
            chat["title"],
            key=f"history_{chat_id}",
            width="stretch",
            type=(
                "primary"
                if chat_id == st.session_state.current_chat
                else "secondary"
            ),
            on_click=switch_chat,
            args=(chat_id,),
        )


st.markdown('''<div class="hero"><div class="eyebrow">YOUR EVERYDAY COMPANION</div>
<h1>留一点时间，给自己。</h1><p>在这里，分享日常，也安放每一种心情。</p></div>''', unsafe_allow_html=True)

if page == "💬 陪伴聊天":
    profile, chat, wellbeing = st.columns([1, 2.35, 1.05], gap="large")

    with profile:
        with st.container(border=True):
            st.markdown(f'''<div class="portrait"><div class="avatar">🌷</div>
<h2>{escape(companion_name or '小暖')}</h2><div class="muted">你的 AI 陪伴伙伴</div>
<span class="tag">{escape(personality)}</span><span class="tag">分享日常</span></div>''', unsafe_allow_html=True)
            st.divider()
            st.markdown("**关于我**")
            st.caption("愿意听你分享生活里的小事，陪你整理思绪，发现日常的美好。")
            st.info("由 DeepSeek 生成回复，聊天内容会发送至 DeepSeek。", icon="✨")

    with chat:
        st.subheader(f"与 {companion_name or '小暖'} 聊聊天")
        st.caption("从一句简单的问候开始")
        history = st.container(height=420, border=True)
        with history:
            for message in st.session_state.messages:
                avatar = "🌷" if message["role"] == "assistant" else "🙂"
                with st.chat_message(message["role"], avatar=avatar):
                    st.write(message["content"])
        if prompt := st.chat_input("想说的话，慢慢告诉我……"):
            send_message(prompt)
            st.rerun()
        quick_topics = ["今天有点累", "分享一件开心的事", "想随便聊聊"]
        for column, topic in zip(st.columns(3), quick_topics):
            with column:
                if st.button(topic, width="stretch"):
                    send_message(topic)
                    st.rerun()
        st.caption("聊天记录自动保存至 SQLite，可在左侧切换历史对话。")

    with wellbeing:
        with st.container(border=True):
            st.markdown("#### 🌤️ 今日心情")
            mood = st.select_slider("你现在感觉怎么样？", options=["低落", "疲惫", "平静", "开心", "超棒"], value="平静")

            if st.button("记录此刻", width="stretch"):
                st.session_state.mood_records.append({"时间": datetime.now().strftime("%Y-%m-%d %H:%M"), "心情": mood})
                st.success("已记录到心情日记")
        st.markdown("#### 🍃 给自己的一句话")
        st.markdown('<div class="note">不必每一天都充满能量，偶尔停下来，也是在照顾自己。</div>', unsafe_allow_html=True)
        st.markdown("#### 本次陪伴")
        st.metric("已分享的消息", sum(m["role"] == "user" for m in st.session_state.messages))

elif page == "📔 心情日记":
    st.subheader("📔 心情日记")
    st.caption("记录来自聊天页的心情打卡，仅在当前会话中保留。")
    if st.session_state.mood_records:
        st.dataframe(st.session_state.mood_records, width="stretch", hide_index=True)
    else:
        st.info("还没有心情记录。到陪伴聊天页，记录一下此刻的心情吧。")

else:
    st.subheader("⚙️ 伴侣设置")
    st.write(f"当前伴侣：{companion_name or '小暖'}")
    st.write(f"陪伴风格：{personality}")
    st.info("可以在左侧调整昵称和风格。后续可在这里扩展模型选择、语音和长期记忆设置。")
