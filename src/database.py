import sqlite3
import hashlib
from datetime import datetime
from typing import Optional, List, Dict, Any
from .config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """Retorna uma conexão configurada com o SQLite."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Cria a tabela de notas fiscais e índices caso não existam."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notas (
                id_unico TEXT PRIMARY KEY,
                origem_contato TEXT NOT NULL,
                numero_nota TEXT NOT NULL,
                status TEXT NOT NULL,
                tentativas INTEGER DEFAULT 0,
                mensagem_erro TEXT,
                criado_em TEXT NOT NULL,
                atualizado_em TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_notas_status ON notas(status)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_notas_numero ON notas(numero_nota)"
        )
        conn.commit()


def gerar_id_unico(origem_contato: str, numero_nota: str) -> str:
    """Gera um hash SHA-256 único para garantir anti-duplicação."""
    chave = f"{origem_contato.strip().lower()}_{numero_nota.strip()}"
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


def inserir_nota(numero_nota: str, origem_contato: str = "WhatsApp") -> bool:
    """
    Insere uma nota com status 'pendente' se ainda não existir.
    Retorna True se for inserida com sucesso ou False se já existir (duplicada local).
    """
    init_db()
    id_unico = gerar_id_unico(origem_contato, numero_nota)
    agora = datetime.now().isoformat()

    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO notas (
                    id_unico, origem_contato, numero_nota, status, tentativas, criado_em, atualizado_em
                ) VALUES (?, ?, ?, 'pendente', 0, ?, ?)
                """,
                (id_unico, origem_contato.strip(), numero_nota.strip(), agora, agora),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Já existe no banco local (anti-duplicação)
            return False


def obter_status_nota(origem_contato: str, numero_nota: str) -> Optional[str]:
    """Retorna o status atual da nota no banco local, ou None se não existir."""
    init_db()
    id_unico = gerar_id_unico(origem_contato, numero_nota)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM notas WHERE id_unico = ?", (id_unico,))
        row = cursor.fetchone()
        return row["status"] if row else None


def obter_notas_pendentes(limite: Optional[int] = None) -> List[Dict[str, Any]]:
    """Obtém as notas pendentes de lançamento."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM notas WHERE status = 'pendente' ORDER BY criado_em ASC"
        if limite and limite > 0:
            query += f" LIMIT {int(limite)}"
        cursor.execute(query)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def atualizar_status_nota(
    id_unico: str,
    novo_status: str,
    mensagem_erro: Optional[str] = None
) -> None:
    """
    Atualiza o status da nota e incrementa tentativas em caso de erro.
    Status válidos: 'pendente', 'lancado', 'duplicada_erp', 'produto_sem_cadastro', 'nota_inexistente', 'erro'
    """
    init_db()
    agora = datetime.now().isoformat()
    incrementar_tentativa = 1 if novo_status in ("erro", "duplicada_erp", "produto_sem_cadastro", "nota_inexistente") else 0

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE notas
            SET status = ?,
                tentativas = tentativas + ?,
                mensagem_erro = ?,
                atualizado_em = ?
            WHERE id_unico = ?
            """,
            (novo_status, incrementar_tentativa, mensagem_erro, agora, id_unico),
        )
        conn.commit()


def obter_resumo_estatisticas() -> Dict[str, int]:
    """Retorna o total de notas por status."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, COUNT(*) as total FROM notas GROUP BY status")
        rows = cursor.fetchall()
        return {row["status"]: row["total"] for row in rows}
