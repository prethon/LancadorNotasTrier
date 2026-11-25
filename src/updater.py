import json
import os
import sys
import re
import ssl
import shutil
import zipfile
import tempfile
import subprocess
import urllib.request
from pathlib import Path
from typing import Dict, Any, Optional, Callable

from .config import BASE_DIR, GITHUB_REPO
from .backup import executar_backup

VERSION_FILE = BASE_DIR / "version.json"

# Arquivos e pastas confidenciais ou locais do cliente que NUNCA podem ser sobrescritos
ITENS_PROTEGIDOS = {
    ".env",
    "data",
    "sessions",
    "reports",
    "backups",
    "__pycache__",
    ".git",
}


def obter_versao_local() -> Dict[str, Any]:
    """Lê a versão atual instalada no version.json."""
    if VERSION_FILE.exists():
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"versao": "1.0.0", "data_lancamento": "2026-11-25", "descricao": "Versão inicial"}


def _normalizar_versao(versao_str: str) -> tuple:
    """Extrai tupla numérica de versão (ex: 'v1.2.3' -> (1, 2, 3))."""
    numeros = re.findall(r"\d+", str(versao_str))
    return tuple(map(int, numeros)) if numeros else (0, 0, 0)


def _abrir_url_com_fallback_ssl(req: urllib.request.Request, timeout: int = 15):
    """
    Abre URL via urllib com fallback automático para contexto sem verificação
    caso o sistema operacional (especialmente macOS) não tenha os certificados locais instalados.
    """
    try:
        ctx = ssl.create_default_context()
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)
    except urllib.error.URLError as e:
        msg_erro = str(e).lower()
        if "certificate_verify_failed" in msg_erro or "certificate verify failed" in msg_erro or "ssl" in msg_erro:
            ctx_bypass = ssl._create_unverified_context()
            return urllib.request.urlopen(req, timeout=timeout, context=ctx_bypass)
        raise e
    except Exception:
        ctx_bypass = ssl._create_unverified_context()
        return urllib.request.urlopen(req, timeout=timeout, context=ctx_bypass)


def verificar_atualizacao(repositorio: Optional[str] = None) -> Dict[str, Any]:
    """
    Consulta o GitHub para verificar se existe uma versão mais recente disponível.
    Utiliza urllib nativo com timeout seguro e User-Agent padrão.
    """
    repo = repositorio or GITHUB_REPO
    versao_local_info = obter_versao_local()
    versao_local = versao_local_info.get("versao", "1.0.0")

    if not repo or repo == "seu_usuario/LancadorNotas":
        return {
            "disponivel": False,
            "motivo": "Repositorio GitHub ainda nao configurado.",
            "versao_atual": versao_local,
        }

    url_raw_version = f"https://raw.githubusercontent.com/{repo}/main/version.json"
    print(f"[Auto-Updater] Checando atualizações em: {url_raw_version}...")

    try:
        req = urllib.request.Request(
            url_raw_version,
            headers={"User-Agent": "LancadorNotas-AutoUpdater/1.0"},
        )
        with _abrir_url_com_fallback_ssl(req, timeout=10) as resposta:
            if resposta.status == 200:
                dados_remotos = json.loads(resposta.read().decode("utf-8"))
                versao_remota = dados_remotos.get("versao", "1.0.0")

                tupla_local = _normalizar_versao(versao_local)
                tupla_remota = _normalizar_versao(versao_remota)

                if tupla_remota > tupla_local:
                    url_download = f"https://github.com/{repo}/archive/refs/heads/main.zip"
                    return {
                        "disponivel": True,
                        "versao_atual": versao_local,
                        "nova_versao": versao_remota,
                        "descricao": dados_remotos.get("descricao", "Melhorias de desempenho e correções."),
                        "data_lancamento": dados_remotos.get("data_lancamento", ""),
                        "url_zip": url_download,
                    }
                else:
                    return {
                        "disponivel": False,
                        "versao_atual": versao_local,
                        "motivo": "Você já está utilizando a versão mais recente.",
                    }

        return {
            "disponivel": False,
            "versao_atual": versao_local,
            "motivo": f"Resposta inesperada do servidor ({resposta.status}).",
        }

    except urllib.error.HTTPError as he:
        if he.code == 404:
            return {
                "disponivel": False,
                "versao_atual": versao_local,
                "motivo": "Repositório ou version.json não encontrado no GitHub (verifique se o repositório é público e se o branch é 'main').",
            }
        return {
            "disponivel": False,
            "versao_atual": versao_local,
            "motivo": f"Erro HTTP {he.code} ao consultar GitHub.",
        }
    except Exception as e:
        return {
            "disponivel": False,
            "versao_atual": versao_local,
            "motivo": f"Falha de conexão com GitHub: {str(e)}",
        }


def baixar_e_aplicar_atualizacao(
    url_zip: str,
    callback_progresso: Optional[Callable[[str], None]] = None,
) -> bool:
    """
    Baixa o código atualizado do GitHub e substitui os arquivos de código
    garantindo que senhas (.env), banco (data/), sessões e relatórios NUNCA sejam tocados.
    """
    def log(msg: str):
        print(f"[Auto-Updater] {msg}")
        if callback_progresso:
            callback_progresso(msg)

    pasta_temp = None
    try:
        # 1. Backup prévio de segurança
        log("Gerando cópia de segurança prévia dos dados locais...")
        executar_backup()

        # 2. Criar diretório temporário para download
        pasta_temp = Path(tempfile.mkdtemp(prefix="update_lancador_"))
        caminho_zip = pasta_temp / "update.zip"

        log("Baixando pacote de atualização da nuvem...")
        req = urllib.request.Request(
            url_zip,
            headers={"User-Agent": "LancadorNotas-AutoUpdater/1.0"},
        )
        with _abrir_url_com_fallback_ssl(req, timeout=60) as resp, open(caminho_zip, "wb") as f_out:
            shutil.copyfileobj(resp, f_out)

        log("Extraindo arquivos da atualização...")
        pasta_extracao = pasta_temp / "extraido"
        with zipfile.ZipFile(caminho_zip, "r") as z:
            z.extractall(pasta_extracao)

        # O GitHub extrai dentro de uma pasta com nome do repo (ex: LancadorNotas-main)
        subpastas = [p for p in pasta_extracao.iterdir() if p.is_dir()]
        raiz_conteudo = subpastas[0] if subpastas else pasta_extracao

        log("Aplicando novos arquivos e preservando dados confidenciais...")

        total_atualizados = 0
        for item in raiz_conteudo.iterdir():
            nome_item = item.name

            # REGRA MÁXIMA: Nunca sobrescreve itens protegidos
            if nome_item in ITENS_PROTEGIDOS:
                continue

            destino_item = BASE_DIR / nome_item

            if item.is_file():
                shutil.copy2(item, destino_item)
                total_atualizados += 1
            elif item.is_dir():
                # Atualiza pastas (como src/) sem apagar arquivos locais extras
                for sub_raiz, _, sub_arquivos in os.walk(item):
                    rel = Path(sub_raiz).relative_to(item)
                    dest_dir = destino_item / rel
                    dest_dir.mkdir(parents=True, exist_ok=True)

                    for arq in sub_arquivos:
                        if arq.endswith(".pyc"):
                            continue
                        origem_arq = Path(sub_raiz) / arq
                        dest_arq = dest_dir / arq
                        shutil.copy2(origem_arq, dest_arq)
                        total_atualizados += 1

        log(f"✅ Atualização concluída com sucesso! ({total_atualizados} arquivos atualizados).")
        return True

    except Exception as e:
        log(f"❌ Falha ao aplicar atualização: {e}")
        return False
    finally:
        if pasta_temp and pasta_temp.exists():
            try:
                shutil.rmtree(pasta_temp, ignore_errors=True)
            except Exception:
                pass


def reiniciar_aplicacao():
    """Reinicia o aplicativo chamando python app.py em um novo processo e encerrando o atual."""
    try:
        script_principal = BASE_DIR / "app.py"
        subprocess.Popen([sys.executable, str(script_principal)])
        os._exit(0)
    except Exception as e:
        print(f"[Auto-Updater] Falha ao reiniciar automaticamente: {e}")
