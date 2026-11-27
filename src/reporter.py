import csv
import time
from datetime import datetime
from pathlib import Path
from .config import REPORTS_DIR

CABECALHO_PADRAO = ["Data_Hora", "Numero_Nota", "Origem_Contato", "Detalhes"]


def _descarregar_buffer_pendente(caminho_arquivo: Path, cabecalho: list) -> None:
    """Mescla linhas represadas no arquivo _buffer.csv assim que o CSV principal estiver liberado."""
    arquivo_buffer = caminho_arquivo.with_name(f"{caminho_arquivo.stem}_buffer.csv")
    if not arquivo_buffer.exists():
        return

    try:
        with open(arquivo_buffer, mode="r", newline="", encoding="utf-8-sig") as fb:
            linhas_buffer = list(csv.reader(fb, delimiter=";"))
        if not linhas_buffer:
            arquivo_buffer.unlink(missing_ok=True)
            return

        arquivo_existe = caminho_arquivo.exists() and caminho_arquivo.stat().st_size > 0
        with open(caminho_arquivo, mode="a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            if not arquivo_existe:
                writer.writerow(cabecalho)
            for linha in linhas_buffer:
                if linha != cabecalho:
                    writer.writerow(linha)

        arquivo_buffer.unlink(missing_ok=True)
        print(f"[Relatório] Buffer pendente mesclado com sucesso em {caminho_arquivo.name}.")
    except Exception:
        pass


def _adicionar_ao_relatorio(caminho_arquivo: Path, cabecalho: list, linha: list) -> None:
    """
    Anexa uma linha em CSV compatível com Excel.
    Se o arquivo principal estiver bloqueado (ex: aberto no Excel), tenta 3x e grava em
    <nome>_buffer.csv para mesclar automaticamente na próxima oportunidade sem perder dados.
    """
    for tentativa in range(3):
        try:
            _descarregar_buffer_pendente(caminho_arquivo, cabecalho)
            arquivo_existe = caminho_arquivo.exists() and caminho_arquivo.stat().st_size > 0

            with open(caminho_arquivo, mode="a", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                if not arquivo_existe:
                    writer.writerow(cabecalho)
                writer.writerow(linha)
            return
        except PermissionError:
            if tentativa < 2:
                time.sleep(0.3)
            else:
                arquivo_buffer = caminho_arquivo.with_name(f"{caminho_arquivo.stem}_buffer.csv")
                try:
                    buffer_existe = arquivo_buffer.exists() and arquivo_buffer.stat().st_size > 0
                    with open(arquivo_buffer, mode="a", newline="", encoding="utf-8-sig") as fb:
                        writer = csv.writer(fb, delimiter=";")
                        if not buffer_existe:
                            writer.writerow(cabecalho)
                        writer.writerow(linha)
                    print(
                        f"[Relatório - Buffer] '{caminho_arquivo.name}' está aberto/bloqueado; "
                        f"registro salvo em '{arquivo_buffer.name}' para mesclagem automática."
                    )
                except Exception as err_buf:
                    print(f"[Relatório - Aviso] Não foi possível gravar buffer de '{caminho_arquivo.name}': {err_buf}")
        except Exception as err:
            print(f"[Relatório - Aviso] Falha ao gravar em '{caminho_arquivo.name}': {err}")
            return


def registrar_nota_lancada(numero_nota: str, origem_contato: str, detalhes: str = "Lançamento concluído com sucesso") -> None:
    """Registra uma nota lançada com sucesso em reports/notas_lancadas.csv."""
    arquivo = REPORTS_DIR / "notas_lancadas.csv"
    _adicionar_ao_relatorio(
        arquivo,
        CABECALHO_PADRAO,
        [datetime.now().strftime("%d/%m/%Y %H:%M:%S"), numero_nota, origem_contato, detalhes]
    )


def registrar_duplicidade(numero_nota: str, origem_contato: str, detalhes: str = "Nota já lançada no ERP") -> None:
    """Registra uma nota em reports/duplicidades.csv."""
    arquivo = REPORTS_DIR / "duplicidades.csv"
    _adicionar_ao_relatorio(
        arquivo,
        CABECALHO_PADRAO,
        [datetime.now().strftime("%d/%m/%Y %H:%M:%S"), numero_nota, origem_contato, detalhes]
    )


def registrar_produto_sem_cadastro(numero_nota: str, origem_contato: str, detalhes: str = "Produtos sem cadastro no ERP") -> None:
    """Registra uma nota em reports/produtos_sem_cadastro.csv."""
    arquivo = REPORTS_DIR / "produtos_sem_cadastro.csv"
    _adicionar_ao_relatorio(
        arquivo,
        CABECALHO_PADRAO,
        [datetime.now().strftime("%d/%m/%Y %H:%M:%S"), numero_nota, origem_contato, detalhes]
    )


def registrar_nota_inexistente(numero_nota: str, origem_contato: str, detalhes: str = "Nota não existe no ERP") -> None:
    """Registra uma nota que não existe no ERP em reports/notas_inexistentes.csv."""
    arquivo = REPORTS_DIR / "notas_inexistentes.csv"
    _adicionar_ao_relatorio(
        arquivo,
        CABECALHO_PADRAO,
        [datetime.now().strftime("%d/%m/%Y %H:%M:%S"), numero_nota, origem_contato, detalhes]
    )


def registrar_outro_erro(numero_nota: str, origem_contato: str, detalhes: str) -> None:
    """Registra uma falha técnica ou imprevisto em reports/outros_erros.csv."""
    arquivo = REPORTS_DIR / "outros_erros.csv"
    _adicionar_ao_relatorio(
        arquivo,
        CABECALHO_PADRAO,
        [datetime.now().strftime("%d/%m/%Y %H:%M:%S"), numero_nota, origem_contato, detalhes]
    )


def registrar_nota_sem_boleto(
    numero_nota: str,
    fornecedor: str,
    valor_nota: str,
    detalhes: str = "Nota sem boletos/duplicatas geradas no Contas a Pagar"
) -> None:
    """Registra notas que não possuem duplicatas/boletos em reports/notas_sem_boleto.csv."""
    arquivo = REPORTS_DIR / "notas_sem_boleto.csv"
    cabecalho = ["Data_Hora", "Numero_Nota", "Fornecedor", "Valor_Nota", "Detalhes"]
    _adicionar_ao_relatorio(
        arquivo,
        cabecalho,
        [datetime.now().strftime("%d/%m/%Y %H:%M:%S"), numero_nota, fornecedor, valor_nota, detalhes]
    )


def registrar_boleto_divergente(
    numero_nota: str,
    fornecedor: str,
    valor_nota: str,
    valor_boleto: str,
    diferenca: str,
    detalhes: str = "Valor do boleto maior que a nota em mais de R$ 7,00"
) -> None:
    """Registra divergências em que o boleto é maior que a nota além de R$ 7 em reports/boletos_divergentes.csv."""
    arquivo = REPORTS_DIR / "boletos_divergentes.csv"
    cabecalho = ["Data_Hora", "Numero_Nota", "Fornecedor", "Valor_Nota", "Valor_Boleto", "Diferenca", "Detalhes"]
    _adicionar_ao_relatorio(
        arquivo,
        cabecalho,
        [datetime.now().strftime("%d/%m/%Y %H:%M:%S"), numero_nota, fornecedor, valor_nota, valor_boleto, diferenca, detalhes]
    )
