import os
import zipfile
from datetime import datetime
from pathlib import Path
from .config import BASE_DIR, DATA_DIR, REPORTS_DIR

BACKUPS_DIR = BASE_DIR / "backups"
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)


def executar_backup(max_backups: int = 15) -> str:
    """
    Compacta automaticamente o banco notas.db e todos os relatórios CSV
    em um arquivo zip dentro da pasta backups/.
    Mantém apenas os últimos max_backups arquivos.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    nome_zip = f"backup_{timestamp}.zip"
    caminho_zip = BACKUPS_DIR / nome_zip

    try:
        with zipfile.ZipFile(caminho_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
            # 1. Banco SQLite
            banco = DATA_DIR / "notas.db"
            if banco.exists():
                zipf.write(banco, arcname="notas.db")

            # 2. Todos os relatórios CSV
            if REPORTS_DIR.exists():
                for arq in REPORTS_DIR.glob("*.csv"):
                    zipf.write(arq, arcname=f"reports/{arq.name}")

            # 3. Checkpoint do WhatsApp
            checkpoint = DATA_DIR / "whatsapp_checkpoint.json"
            if checkpoint.exists():
                zipf.write(checkpoint, arcname="whatsapp_checkpoint.json")

        print(f"[Backup] 💾 Backup de segurança gerado com sucesso: backups/{nome_zip}")

        # Limpeza de backups antigos
        backups_existentes = sorted(
            BACKUPS_DIR.glob("backup_*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if len(backups_existentes) > max_backups:
            for antigo in backups_existentes[max_backups:]:
                try:
                    antigo.unlink()
                except Exception:
                    pass

        return str(caminho_zip)

    except Exception as e:
        print(f"[Backup] Aviso ao gerar backup: {e}")
        return ""
