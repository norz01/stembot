import os
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException, Depends, status, Body
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from jose import JWTError, jwt
from passlib.context import CryptContext

# Import fungsi sedia ada anda (mungkin perlu sedikit penyesuaian)
# Anda perlu letakkan fungsi-fungsi ini dalam fail berasingan atau di sini.
# Untuk contoh ini, saya akan letakkan versi ringkasnya di sini.
import requests 

# --- KONFIGURASI ---
OLLAMA_BASE_URL = "http://localhost:11434"
USERS_DIR = "user_data"
USERS_FILE = os.path.join(USERS_DIR, "users.json")
HISTORY_DIR = "chat_sessions"

# Konfigurasi Keselamatan untuk JWT
SECRET_KEY = "your-super-secret-key-change-this" # TUKAR INI! Guna 'openssl rand -hex 32' untuk jana kunci
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Pastikan direktori wujud
os.makedirs(USERS_DIR, exist_ok=True)
os.makedirs(HISTORY_DIR, exist_ok=True)

# --- MODEL DATA (Pydantic) ---
class Token(BaseModel):
    access_token: str
    token_type: str

class User(BaseModel):
    username: str

class ChatMessage(BaseModel):
    role: str
    content: str
    thinking_process: str | None = None
    time_taken: float | None = None

class ChatHistory(BaseModel):
    messages: List[ChatMessage]

class ChatRequest(BaseModel):
    prompt: str
    chat_history: List[Dict[str, Any]]
    selected_model: str

# --- PENGURUSAN KATA LALUAN & PENGESAHAN ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/token")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def load_users():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, "w") as f: json.dump({}, f)
    with open(USERS_FILE, "r") as f: return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f: json.dump(users, f, indent=2)

def authenticate_user(username, password):
    users = load_users()
    if username not in users:
        return False
    user = users[username]
    if not verify_password(password, user["password"]):
        return False
    return user

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    users = load_users()
    user = users.get(username)
    if user is None:
        raise credentials_exception
    return User(username=username)

# --- FUNGSI LOGIK UTAMA (diadaptasi dari kod anda) ---
def get_user_history_dir(username: str):
    user_dir = os.path.join(HISTORY_DIR, username)
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def load_all_session_ids_for_user(username: str):
    user_history_dir = get_user_history_dir(username)
    # ... (logik sort_key anda di sini) ...
    try:
        files = [f.replace(".json", "") for f in os.listdir(user_history_dir) if f.endswith(".json")]
        return sorted(files, reverse=True) # Versi ringkas
    except:
        return []

def load_chat_session_for_user(username: str, session_id: str):
    user_history_dir = get_user_history_dir(username)
    filepath = os.path.join(user_history_dir, f"{session_id}.json")
    try:
        with open(filepath, "r") as f: return json.load(f)
    except FileNotFoundError:
        return []

def save_chat_session_for_user(username: str, session_id: str, history: List[Dict]):
    user_history_dir = get_user_history_dir(username)
    filepath = os.path.join(user_history_dir, f"{session_id}.json")
    with open(filepath, "w") as f: json.dump(history, f, indent=2)

def query_ollama(prompt: str, chat_history: List[Dict], selected_model: str):
    # Ini adalah versi ringkas dari query_ollama_non_stream anda
    messages_for_api = chat_history + [{"role": "user", "content": prompt}]
    try:
        payload = {'model': selected_model, 'messages': messages_for_api, 'stream': False}
        response = requests.post(f'{OLLAMA_BASE_URL}/api/chat', json=payload, timeout=600)
        response.raise_for_status()
        data = response.json()
        return data.get('message', {})
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Ollama service unavailable: {e}")


# --- INISIALISASI APLIKASI FastAPI ---
app = FastAPI()

# Konfigurasi CORS (PENTING untuk pembangunan tempatan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Alamat SvelteKit dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# === ENDPOINTS API ===

@app.post("/api/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": form_data.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/api/register")
async def register_user(username: str = Body(...), password: str = Body(...)):
    users = load_users()
    if username in users:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    hashed_password = get_password_hash(password)
    users[username] = {
        "password": hashed_password,
        "created_at": datetime.now().isoformat()
    }
    save_users(users)
    return {"message": "User registered successfully"}

@app.get("/api/users/me", response_model=User)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    response_message = query_ollama(request.prompt, request.chat_history, request.selected_model)
    if not response_message:
        raise HTTPException(status_code=500, detail="Failed to get response from Ollama model")
    
    # Di sini anda boleh menambah logik untuk memisahkan "thinking process" jika mahu
    # Untuk kesederhanaan, kita kembalikan mesej penuh dahulu
    return response_message

@app.get("/api/sessions")
async def get_sessions(current_user: User = Depends(get_current_user)):
    return {"sessions": load_all_session_ids_for_user(current_user.username)}

@app.get("/api/sessions/{session_id}")
async def get_session_history(session_id: str, current_user: User = Depends(get_current_user)):
    history = load_chat_session_for_user(current_user.username, session_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"history": history}

@app.post("/api/sessions")
async def save_session(session_id: str = Body(...), history: List[Dict] = Body(...), current_user: User = Depends(get_current_user)):
    save_chat_session_for_user(current_user.username, session_id, history)
    return {"message": "Session saved successfully"}

# Untuk menjalankan server:
# Buka terminal dan taip: uvicorn backend_api:app --reload
if __name__ == "__main__":
    uvicorn.run("backend_api:app", host="127.0.0.1", port=8000, reload=True)
