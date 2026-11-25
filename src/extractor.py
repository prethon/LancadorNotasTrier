import re
from typing import List


def extrair_numeros_notas(texto: str) -> List[str]:
    """
    Como o WhatsApp é exclusivo para o recebimento de notas fiscais,
    qualquer mensagem recebida é tratada diretamente como número de nota.

    Suporta:
    - Um número isolado: "5706510" ou "5.706.510"
    - Múltiplos números em linhas separadas:
        "5706510
         5706511
         5706512"
    - Múltiplos números separados por vírgula, espaço ou ponto e vírgula.
    """
    if not texto:
        return []

    notas: List[str] = []
    # Quebra o texto por linhas, vírgulas ou ponto e vírgula
    linhas = re.split(r"[\r\n;,]+", texto)

    for linha in linhas:
        item = linha.strip()
        if not item:
            continue

        # Mantém apenas os dígitos da nota (removendo pontos, traços ou espaços acidentais)
        apenas_digitos = re.sub(r"\D", "", item)

        # Considera qualquer sequência numérica válida de nota fiscal (2 a 9 dígitos)
        if 2 <= len(apenas_digitos) <= 9 and apenas_digitos not in notas:
            notas.append(apenas_digitos)

    return notas
