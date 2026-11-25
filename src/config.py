import os
from pathlib import Path
from dotenv import load_dotenv

# Diretório base do projeto (relativo e portátil)
BASE_DIR = Path(__file__).resolve().parent.parent

# Diretórios estruturais
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
SESSIONS_DIR = BASE_DIR / "sessions"
WHATSAPP_SESSION_DIR = SESSIONS_DIR / "whatsapp"
ERP_SESSION_DIR = SESSIONS_DIR / "erp"

# Caminho do banco SQLite local
DB_PATH = DATA_DIR / "notas.db"

# Garantir existência automática das pastas necessárias
for directory in (DATA_DIR, REPORTS_DIR, SESSIONS_DIR, WHATSAPP_SESSION_DIR, ERP_SESSION_DIR):
    directory.mkdir(parents=True, exist_ok=True)

# Carregar variáveis do .env (se existir)
load_dotenv(BASE_DIR / ".env")

# Configurações de execução
MODO_TESTE = os.getenv("MODO_TESTE", "true").strip().lower() in ("true", "1", "yes", "sim")
LIMITE_NOTAS_POR_EXECUCAO = int(os.getenv("LIMITE_NOTAS_POR_EXECUCAO", "1"))
INTERVALO_MONITORAMENTO_SEGUNDOS = int(os.getenv("INTERVALO_MONITORAMENTO_SEGUNDOS", "10"))

# Configurações do WhatsApp Web
WHATSAPP_CHAT_ALVO = os.getenv("WHATSAPP_CHAT_ALVO", "").strip()

# Configurações do ERP
ERP_URL = os.getenv("ERP_URL", "").strip()
ERP_USUARIO = os.getenv("ERP_USUARIO", "").strip()
ERP_SENHA = os.getenv("ERP_SENHA", "").strip()

# Configurações de Atualização Automática (GitHub)
GITHUB_REPO = os.getenv("GITHUB_REPO", "prethon/LancadorNotasTrier").strip()
