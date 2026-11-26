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

        # Ignora linhas que são horários típicos do WhatsApp (ex: "14:35", "09:12", "14:35 ✓")
        if re.match(r"^\s*\d{1,2}:\d{2}(:\d{2})?(\s*(am|pm|[✓✔\s]))*$", item, re.IGNORECASE):
            continue

        # Ignora linhas que são puramente datas (ex: "25/11/2026", "25/11/26")
        if re.match(r"^\s*\d{1,2}/\d{1,2}/\d{2,4}\s*$", item):
            continue

        tem_letras = bool(re.search(r"[A-Za-zÀ-ÿ]", item))
        min_digitos = 4 if tem_letras else 2

        # Extrai blocos numéricos individuais (suporta ponto de milhar como "5.706.510"
        # sem concatenar números separados como "Loja 12 NF 7485624")
        tokens_numericos = re.findall(r"\d+(?:\.\d{3})*", item)
        for token in tokens_numericos:
            apenas_digitos = re.sub(r"\D", "", token)
            if min_digitos <= len(apenas_digitos) <= 9 and apenas_digitos not in notas:
                notas.append(apenas_digitos)

    return notas
