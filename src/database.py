import sqlite3
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from .config import DB_PATH


def get_connection() -> sqlite3.Connection:
    """Retorna uma conexão configurada com o SQLite (com timeout anti-lock)."""
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Cria a tabela de notas fiscais, índices e ativa modo WAL caso não existam."""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS notas (
                id_unico TEXT PRIMARY KEY,
                origem_contato TEXT NOT NULL,
                chat_origem TEXT,
                numero_nota TEXT NOT NULL,
                status TEXT NOT NULL,
                tentativas INTEGER DEFAULT 0,
                mensagem_erro TEXT,
                criado_em TEXT NOT NULL,
                atualizado_em TEXT NOT NULL
            )
            """
        )
        try:
            cursor.execute("ALTER TABLE notas ADD COLUMN chat_origem TEXT")
        except sqlite3.OperationalError:
            pass
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


def inserir_nota(numero_nota: str, origem_contato: str = "WhatsApp", chat_origem: Optional[str] = None) -> bool:
    """
    Insere uma nota com status 'pendente' se ainda não existir.
    Retorna True se for inserida com sucesso ou False se já existir (duplicada local).
    """
    init_db()
    id_unico = gerar_id_unico(origem_contato, numero_nota)
    chat_limpo = (chat_origem or origem_contato).strip()
    agora = datetime.now().isoformat()

    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO notas (
                    id_unico, origem_contato, chat_origem, numero_nota, status, tentativas, criado_em, atualizado_em
                ) VALUES (?, ?, ?, ?, 'pendente', 0, ?, ?)
                """,
                (id_unico, origem_contato.strip(), chat_limpo, numero_nota.strip(), agora, agora),
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


def obter_nota_por_id(id_unico: str) -> Optional[Dict[str, Any]]:
    """Retorna o registro completo da nota pelo id_unico, ou None."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM notas WHERE id_unico = ?", (id_unico,))
        row = cursor.fetchone()
        return dict(row) if row else None


def agendar_verificacao_duplicidade(
    origem_contato: str,
    numero_nota: str,
    chat_origem: Optional[str] = None,
) -> Optional[str]:
    """
    Quando uma nota marcada como 'lancado' ou 'duplicada_erp' é reenviada no WhatsApp,
    agenda status 'verificar_duplicidade' para conferência real no ERP (sem perder o status original).
    Retorna o status anterior caso agendado, ou None se não aplicável.
    """
    init_db()
    id_unico = gerar_id_unico(origem_contato, numero_nota)
    agora = datetime.now().isoformat()
    chat_limpo = chat_origem.strip() if chat_origem and chat_origem.strip() else None
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM notas WHERE id_unico = ?", (id_unico,))
        row = cursor.fetchone()
        if not row:
            return None
        status_ant = row["status"]
        if status_ant not in ("lancado", "duplicada_erp"):
            return None
        cursor.execute(
            """
            UPDATE notas
            SET status = 'verificar_duplicidade',
                tentativas = 0,
                chat_origem = COALESCE(?, chat_origem),
                mensagem_erro = ?,
                atualizado_em = ?
            WHERE id_unico = ?
            """,
            (chat_limpo, f"recheck_duplicidade:{status_ant}", agora, id_unico),
        )
        conn.commit()
        return status_ant


def reabrir_nota_por_numero(numero_nota: str) -> int:
    """
    Recoloca todas as ocorrências de uma NF específica na fila como 'pendente' (zerando tentativas).
    Se não existir no banco local, insere uma nova entrada com origem 'Painel_Manual'.
    Retorna a quantidade de registros colocados na fila.
    """
    init_db()
    num_limpo = numero_nota.strip()
    if not num_limpo:
        return 0
    agora = datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE notas
            SET status = 'pendente',
                tentativas = 0,
                mensagem_erro = NULL,
                atualizado_em = ?
            WHERE numero_nota = ?
            """,
            (agora, num_limpo),
        )
        atualizados = cursor.rowcount
        conn.commit()
    if atualizados == 0:
        inserir_nota(numero_nota=num_limpo, origem_contato="Painel_Manual")
        return 1
    return atualizados


def obter_notas_pendentes(
    limite: Optional[int] = None,
    incluir_em_cooldown: bool = False,
    cooldown_segundos: int = 30,
) -> List[Dict[str, Any]]:
    """
    Obtém as notas pendentes, em verificação de reenvio ou aguardando retry após cooldown.
    Por padrão filtra notas em 'aguardando_retry' cujo cooldown ainda não expirou.
    """
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        if incluir_em_cooldown:
            query = """
                SELECT * FROM notas
                WHERE status IN ('pendente', 'verificar_duplicidade', 'aguardando_retry')
                ORDER BY CASE
                    WHEN status = 'pendente' THEN 0
                    WHEN status = 'verificar_duplicidade' THEN 1
                    ELSE 2
                END, criado_em ASC
            """
            params: List[Any] = []
        else:
            cutoff_iso = (datetime.now() - timedelta(seconds=cooldown_segundos)).isoformat()
            query = """
                SELECT * FROM notas
                WHERE status IN ('pendente', 'verificar_duplicidade')
                   OR (status = 'aguardando_retry' AND atualizado_em <= ?)
                ORDER BY CASE
                    WHEN status = 'pendente' THEN 0
                    WHEN status = 'verificar_duplicidade' THEN 1
                    ELSE 2
                END, criado_em ASC
            """
            params = [cutoff_iso]

        if limite and limite > 0:
            query += f" LIMIT {int(limite)}"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def atualizar_status_nota(
    id_unico: str,
    novo_status: str,
    mensagem_erro: Optional[str] = None
) -> None:
    """
    Atualiza o status da nota e incrementa tentativas em caso de erro.
    Status válidos: 'pendente', 'verificar_duplicidade', 'aguardando_retry', 'lancado', 'duplicada_erp', 'produto_sem_cadastro', 'nota_inexistente', 'erro'
    """
    init_db()
    agora = datetime.now().isoformat()
    incrementar_tentativa = 1 if novo_status in ("erro", "aguardando_retry", "duplicada_erp", "produto_sem_cadastro", "nota_inexistente") else 0

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


def registrar_falha_lancamento(
    id_unico: str,
    mensagem_erro: Optional[str] = None,
    max_tentativas: int = 3,
) -> Tuple[str, int]:
    """
    Registra uma falha técnica no lançamento da NF.
    Enquanto não atingir max_tentativas, marca como 'aguardando_retry' para tentar novamente
    automaticamente após cooldown; ao atingir max_tentativas, marca definitivamente como 'erro'.
    Retorna (novo_status, total_tentativas).
    """
    init_db()
    agora = datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT tentativas FROM notas WHERE id_unico = ?", (id_unico,))
        row = cursor.fetchone()
        tentativas_atuais = int(row["tentativas"] or 0) if row else 0
        nova_tentativa = tentativas_atuais + 1
        novo_status = "aguardando_retry" if nova_tentativa < max_tentativas else "erro"

        cursor.execute(
            """
            UPDATE notas
            SET status = ?,
                tentativas = ?,
                mensagem_erro = ?,
                atualizado_em = ?
            WHERE id_unico = ?
            """,
            (novo_status, nova_tentativa, mensagem_erro, agora, id_unico),
        )
        conn.commit()
        return novo_status, nova_tentativa


def reprocessar_erros() -> int:
    """
    Move todas as notas com status 'erro' de volta para 'pendente' (zerando tentativas)
    para que sejam reprocessadas no próximo ciclo. Retorna a quantidade reativada.
    """
    init_db()
    agora = datetime.now().isoformat()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE notas
            SET status = 'pendente',
                tentativas = 0,
                mensagem_erro = NULL,
                atualizado_em = ?
            WHERE status = 'erro'
            """,
            (agora,),
        )
        conn.commit()
        return cursor.rowcount


def obter_resumo_estatisticas() -> Dict[str, int]:
    """Retorna o total de notas por status."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, COUNT(*) as total FROM notas GROUP BY status")
        rows = cursor.fetchall()
        return {row["status"]: row["total"] for row in rows}
