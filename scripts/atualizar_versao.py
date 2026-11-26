"""Atualiza automaticamente a versão patch, data e descrição em version.json."""

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
VERSION_FILE = BASE_DIR / "version.json"


def incrementar_versao(versao_atual: str) -> str:
    """Incrementa o terceiro segmento (patch) de uma versão X.Y.Z."""
    partes = [int(p) for p in str(versao_atual).strip().split(".") if p.isdigit()]
    while len(partes) < 3:
        partes.append(0)
    partes[2] += 1
    return ".".join(str(p) for p in partes[:3])


def obter_arquivos_staged() -> list[str]:
    """Retorna a lista de arquivos em stage no Git."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            check=True,
        )
        return [linha.strip() for linha in res.stdout.splitlines() if linha.strip()]
    except Exception:
        return []


def gerar_novos_dados(dados_atuais: dict, descricao: str = "", usar_staged: bool = False) -> dict:
    """Calcula o próximo conteúdo do version.json sem gravar em disco."""
    versao_nova = incrementar_versao(dados_atuais.get("versao", "1.0.0"))
    data_hoje = datetime.now().strftime("%Y-%m-%d")

    desc_final = descricao.strip()
    if not desc_final and usar_staged:
        staged = [arq for arq in obter_arquivos_staged() if arq != "version.json"]
        if staged:
            resumo_arqs = ", ".join(staged[:4])
            if len(staged) > 4:
                resumo_arqs += f" (+{len(staged) - 4})"
            desc_final = f"Atualização automática: {resumo_arqs}"

    if not desc_final:
        desc_final = f"Atualização v{versao_nova}"

    return {
        "versao": versao_nova,
        "data_lancamento": data_hoje,
        "descricao": desc_final,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Incrementa a versão do version.json.")
    parser.add_argument("--descricao", type=str, default="", help="Descrição da versão.")
    parser.add_argument("--staged", action="store_true", help="Gera descrição a partir dos arquivos no stage do Git.")
    parser.add_argument("--dry-run", action="store_true", help="Mostra o resultado sem alterar o version.json.")
    args = parser.parse_args()

    dados_atuais = {"versao": "1.0.0", "data_lancamento": "", "descricao": ""}
    if VERSION_FILE.exists():
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            dados_atuais = json.load(f)

    novos_dados = gerar_novos_dados(dados_atuais, descricao=args.descricao, usar_staged=args.staged)

    if args.dry_run:
        print(json.dumps(novos_dados, ensure_ascii=False, indent=2))
        return

    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump(novos_dados, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"[Versão] version.json atualizado: {dados_atuais.get('versao')} -> {novos_dados['versao']}")


if __name__ == "__main__":
    main()
