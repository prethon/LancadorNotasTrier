import csv
from datetime import datetime
from pathlib import Path
from .config import REPORTS_DIR

CABECALHO_PADRAO = ["Data_Hora", "Numero_Nota", "Origem_Contato", "Detalhes"]


def _adicionar_ao_relatorio(caminho_arquivo: Path, cabecalho: list, linha: list) -> None:
    """Função utilitária interna para anexar uma linha com formato compatível com Excel."""
    arquivo_existe = caminho_arquivo.exists()

    with open(caminho_arquivo, mode="a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        if not arquivo_existe:
            writer.writerow(cabecalho)
        writer.writerow(linha)


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
