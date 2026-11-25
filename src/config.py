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
WHATSAPP_ALERTA = os.getenv("WHATSAPP_ALERTA", "").strip()

# Configurações do ERP
ERP_URL = os.getenv("ERP_URL", "").strip()
ERP_USUARIO = os.getenv("ERP_USUARIO", "").strip()
ERP_SENHA = os.getenv("ERP_SENHA", "").strip()

# Configurações de Atualização Automática (GitHub)
GITHUB_REPO = os.getenv("GITHUB_REPO", "prethon/LancadorNotasTrier").strip()


def salvar_env(novos_dados: dict) -> None:
    """Atualiza ou cria o arquivo .env mantendo as variáveis persistentes."""
    env_file = BASE_DIR / ".env"
    linhas_existentes = []
    chaves_atualizadas = set()

    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                linhas_existentes = f.readlines()
        except Exception:
            linhas_existentes = []

    novas_linhas = []
    for l in linhas_existentes:
        l_strip = l.strip()
        if "=" in l_strip and not l_strip.startswith("#"):
            chave = l_strip.split("=", 1)[0].strip()
            if chave in novos_dados:
                novas_linhas.append(f"{chave}={novos_dados[chave]}\n")
                chaves_atualizadas.add(chave)
                continue
        novas_linhas.append(l)

    for chave, valor in novos_dados.items():
        os.environ[chave] = str(valor)
        if chave not in chaves_atualizadas:
            novas_linhas.append(f"{chave}={valor}\n")

    try:
        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(novas_linhas)
    except Exception as e:
        print(f"[Config] Aviso ao salvar .env: {e}")

