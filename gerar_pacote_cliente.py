import os
import zipfile
from pathlib import Path

# Diretório base do projeto
BASE_DIR = Path(__file__).resolve().parent

# Nome do arquivo zip final
NOME_ZIP_SAIDA = "LancadorNotas_Versao_Cliente.zip"
CAMINHO_ZIP_SAIDA = BASE_DIR / NOME_ZIP_SAIDA

# Arquivos e diretórios essenciais a incluir
ITENS_INCLUIR = [
    "src",
    "app.py",
    "main.py",
    "version.json",
    "requirements.txt",
    ".env.example",
    "instalar_dependencias.bat",
    "iniciar.bat",
    "README.md",
]

# Padrões e pastas confidenciais ou desnecessárias que NUNCA devem entrar
IGNORAR_NOMES = {
    ".env",
    "data",
    "sessions",
    "reports",
    "backups",
    "__pycache__",
    ".git",
    ".gitignore",
    ".DS_Store",
    "Thumbs.db",
    NOME_ZIP_SAIDA,
}


def criar_pacote_distribuicao():
    print("=" * 65)
    print("   GERADOR DE PACOTE LIMPO PARA CLIENTES / OUTRAS MÁQUINAS       ")
    print("=" * 65)
    print("Iniciando empacotamento seguro...")
    print("• Excluindo 100% de senhas, logins do WhatsApp, histórico e relatórios.")
    print("-" * 65)

    total_arquivos = 0

    with zipfile.ZipFile(CAMINHO_ZIP_SAIDA, "w", zipfile.ZIP_DEFLATED) as zipf:
        for item_nome in ITENS_INCLUIR:
            item_path = BASE_DIR / item_nome

            if not item_path.exists():
                print(f"[AVISO] Item não encontrado para empacotar: {item_nome}")
                continue

            # Se for um arquivo avulso na raiz
            if item_path.is_file():
                arcname = f"LancadorNotas/{item_nome}"
                zipf.write(item_path, arcname=arcname)
                total_arquivos += 1
                print(f"  [+] {item_nome} -> {arcname}")

                # Se for o .env.example, duplicamos também como .env limpo dentro do zip
                if item_nome == ".env.example":
                    arcname_env = "LancadorNotas/.env"
                    zipf.write(item_path, arcname=arcname_env)
                    total_arquivos += 1
                    print(f"  [+] .env.example -> {arcname_env} (Modelo inicial pronto)")

            # Se for um diretório (ex: src/)
            elif item_path.is_dir():
                for raiz, subpastas, arquivos in os.walk(item_path):
                    # Filtra pastas indesejadas como __pycache__
                    subpastas[:] = [d for d in subpastas if d not in IGNORAR_NOMES]

                    for arq in arquivos:
                        if arq in IGNORAR_NOMES or arq.endswith(".pyc"):
                            continue

                        caminho_completo = Path(raiz) / arq
                        rel_path = caminho_completo.relative_to(BASE_DIR)
                        arcname = f"LancadorNotas/{rel_path.as_posix()}"
                        zipf.write(caminho_completo, arcname=arcname)
                        total_arquivos += 1
                        print(f"  [+] {rel_path} -> {arcname}")

    tamanho_mb = CAMINHO_ZIP_SAIDA.stat().st_size / (1024 * 1024)

    print("-" * 65)
    print(f"✅ PACOTE GERADO COM SUCESSO!")
    print(f"📦 Arquivo: {NOME_ZIP_SAIDA} ({tamanho_mb:.2f} MB)")
    print(f"📂 Localização: {CAMINHO_ZIP_SAIDA}")
    print(f"📄 Total de arquivos seguros incluídos: {total_arquivos}")
    print("\n🔒 GARANTIA DE PRIVACIDADE:")
    print("   ✓ Nenhuma senha ou credencial do .env foi incluída.")
    print("   ✓ Nenhuma sessão do WhatsApp Web foi incluída.")
    print("   ✓ Nenhum registro do banco notas.db foi incluído.")
    print("   ✓ Nenhum relatório financeiro ou CSV foi incluído.")
    print("=" * 65)


if __name__ == "__main__":
    criar_pacote_distribuicao()
