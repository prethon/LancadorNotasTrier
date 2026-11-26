import re
import time
from typing import Dict, Any, Optional
from playwright.sync_api import sync_playwright, BrowserContext, Page, Dialog
from .config import (
    ERP_SESSION_DIR,
    ERP_URL,
    ERP_USUARIO,
    ERP_SENHA,
    MODO_TESTE,
)
from .database import atualizar_status_nota
from .reporter import (
    registrar_nota_lancada,
    registrar_duplicidade,
    registrar_produto_sem_cadastro,
    registrar_nota_inexistente,
    registrar_outro_erro,
    registrar_nota_sem_boleto,
    registrar_boleto_divergente,
)


class ERPLancador:
    def __init__(
        self,
        headless: Optional[bool] = None,
        url: Optional[str] = None,
        usuario: Optional[str] = None,
        senha: Optional[str] = None,
        playwright_instance=None,
    ):
        if headless is None:
            self.headless = not MODO_TESTE
        else:
            self.headless = headless

        self.url = (url or ERP_URL).strip()
        self.usuario = (usuario or ERP_USUARIO).strip()
        self.senha = (senha or ERP_SENHA).strip()

        self._external_playwright = playwright_instance is not None
        self._playwright = playwright_instance
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.dialog_mensagem: Optional[str] = None

    def __enter__(self):
        self.iniciar()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.fechar()

    def iniciar(self) -> None:
        """Inicializa o navegador com perfil persistente para o ERP."""
        if not self._playwright:
            self._playwright = sync_playwright().start()

        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--disable-infobars",
        ]
        user_agent_desktop = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(ERP_SESSION_DIR),
            headless=self.headless,
            viewport={"width": 1366, "height": 850},
            user_agent=user_agent_desktop,
            locale="pt-BR",
            args=args,
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.on("dialog", self._ao_receber_dialogo)

    def _ao_receber_dialogo(self, dialog: Dialog) -> None:
        """Captura mensagens de alerta nativas do navegador."""
        self.dialog_mensagem = dialog.message
        print(f"[ERP - Alerta do Sistema] {dialog.message}")
        try:
            dialog.accept()
        except Exception:
            pass

    def verificar_ou_recuperar_sessao(self) -> bool:
        """
        Auto-recuperação de sessão: detecta se a sessão do ERP expirou ou foi desconectada
        e refaz o login automaticamente sem travar o robô.
        """
        try:
            if not self.page or self.page.is_closed():
                print("[ERP - Auto-Recuperação] 🔄 Janela do ERP fechada detectada! Reabrindo e reconectando...")
                self.iniciar()
                return self.login_e_navegar_para_entrada()

            url_atual = self.page.url.lower()

            # 1. Se foi deslogado (voltou para login.pod ou tela de login inicial visível)
            precisa_login = (
                "login.pod" in url_atual
                or self.page.get_by_role("textbox", name="Usuário", exact=True).count() > 0
            )
            if precisa_login:
                # Confirma se já não está dentro do módulo Rec_1020 antes de forçar login
                texto_rapido = ""
                try:
                    texto_rapido = self.page.inner_text("body").upper()
                except Exception:
                    pass
                if "NÃO IMPORTADAS" not in texto_rapido and "IMPORTAR [F2]" not in texto_rapido:
                    print("[ERP - Auto-Recuperação] 🔄 Sessão expirada detectada! Reconectando ao ERP automaticamente...")
                    return self.login_e_navegar_para_entrada()

            # 2. Se a segunda tela de senha (Usuário*) estiver visível, confirma ela
            campo_usuario_sec = self.page.get_by_role("textbox", name="Usuário*")
            if campo_usuario_sec.count() > 0:
                print("[ERP - Auto-Recuperação] Confirmando autenticação secundária do módulo de Entrada...")
                campo_usuario_sec.first.fill(self.usuario)
                self.page.get_by_role("textbox", name="Senha*").first.fill(self.senha)
                self.page.get_by_role("button", name="OK").click()
                time.sleep(2)
                return True

            # 3. Se já estiver na tela de Entrada de NF-e (Rec_1020), não faz nada, está perfeita!
            texto_pagina = self.page.inner_text("body").upper()
            if any(termo in texto_pagina for termo in ["NÃO IMPORTADAS", "NAO IMPORTADAS", "FILTROS AVANÇADOS", "FILTROS AVANCADOS", "REC_1020", "IMPORTAR [F2]"]):
                return True

            # 4. Se estiver logado mas fora da tela de Entrada (ex: no menu principal)
            print("[ERP - Auto-Recuperação] Reposicionando na tela de Entrada de NF-e através do menu...")
            return self.navegar_menu_para_entrada()

        except Exception as e:
            if "closed" in str(e).lower():
                try:
                    print("[ERP - Auto-Recuperação] 🔄 Navegador do ERP foi fechado. Reabrindo sessão...")
                    self.iniciar()
                    return self.login_e_navegar_para_entrada()
                except Exception as err_reabrir:
                    print(f"[ERP - Auto-Recuperação] Falha ao reabrir ERP: {err_reabrir}")
                    return False
            print(f"[ERP - Auto-Recuperação] Aviso ao checar sessão: {e}")
            return True

    def navegar_menu_para_entrada(self) -> bool:
        """Navega pelo menu Compras -> Entradas -> Entrada NF-e sem refazer o login inicial."""
        try:
            print("[ERP] Navegando: Compras -> Entradas de Mercadorias -> Entrada de Mercadorias NF-e...")
            self.page.locator("a").filter(has_text=re.compile(r"^Compras$")).first.click(timeout=8000)
            time.sleep(1)
            self.page.locator("a").filter(has_text=re.compile(r"^Entradas de Mercadorias$")).click(timeout=8000)
            time.sleep(1)
            self.page.get_by_role("link", name="Entrada de Mercadorias NF-e").click(timeout=8000)
            time.sleep(2)

            campo_usuario_sec = self.page.get_by_role("textbox", name="Usuário*")
            if campo_usuario_sec.count() > 0:
                print("[ERP] Confirmando autenticação do módulo de Entrada de NF-e...")
                campo_usuario_sec.first.fill(self.usuario)
                self.page.get_by_role("textbox", name="Senha*").first.fill(self.senha)
                self.page.get_by_role("button", name="OK").click()
                time.sleep(2)

            return True
        except Exception as e:
            print(f"[ERP] Aviso ao navegar no menu: {e}. Tentando login completo...")
            return self.login_e_navegar_para_entrada()

    def login_e_navegar_para_entrada(self) -> bool:
        """Executa o login no ERP e navega até a tela de Entrada de Mercadorias NF-e."""
        try:
            print(f"[ERP] Acessando Central ERP: {self.url}...")
            self.page.goto(self.url, wait_until="load", timeout=60000)

            # 1. Login inicial
            campo_usuario = self.page.get_by_role("textbox", name="Usuário")
            if campo_usuario.count() > 0:
                print(f"[ERP] Efetuando login inicial (Usuário: {self.usuario})...")
                campo_usuario.first.fill(self.usuario)
                self.page.get_by_role("textbox", name="Senha").first.fill(self.senha)
                self.page.get_by_role("button", name="Login [F2]").click()
                time.sleep(2)

            # 2. Navegação no menu
            print("[ERP] Navegando: Compras -> Entradas de Mercadorias -> Entrada de Mercadorias NF-e...")
            try:
                self.page.locator("a").filter(has_text=re.compile(r"^Compras$")).first.click(timeout=10000)
                time.sleep(1)
                self.page.locator("a").filter(has_text=re.compile(r"^Entradas de Mercadorias$")).click(timeout=10000)
                time.sleep(1)
                self.page.get_by_role("link", name="Entrada de Mercadorias NF-e").click(timeout=10000)
                time.sleep(2)
            except Exception as e:
                print(f"[ERP] Aviso na navegação do menu (verificando se já está na tela): {e}")

            # 3. Segunda autenticação
            campo_usuario_sec = self.page.get_by_role("textbox", name="Usuário*")
            if campo_usuario_sec.count() > 0:
                print("[ERP] Confirmando autenticação do módulo de Entrada de NF-e...")
                campo_usuario_sec.first.fill(self.usuario)
                self.page.get_by_role("textbox", name="Senha*").first.fill(self.senha)
                self.page.get_by_role("button", name="OK").click()
                time.sleep(2)

            print("[ERP] Tela de Entrada de Mercadorias NF-e pronta.")
            return True
        except Exception as e:
            print(f"[ERP - Erro de Conexão] Falha ao acessar/logar no ERP: {e}")
            return False

    def _selecionar_filtro(self, nome_opcao: str) -> None:
        """Seleciona o botão de opção (radio) no grupo Seleção (Não Importadas / Importadas)."""
        try:
            radio = self.page.get_by_label(re.compile(f"^{re.escape(nome_opcao)}", re.IGNORECASE))
            if radio.count() > 0:
                radio.first.check()
                time.sleep(0.5)
                return

            rotulo = self.page.locator(f"label:has-text('{nome_opcao}'), span:has-text('{nome_opcao}')")
            if rotulo.count() > 0:
                rotulo.first.click()
                time.sleep(0.5)
                return

            termo = "nao" if "não" in nome_opcao.lower() else "import"
            self.page.locator(f"input[type='radio'][value*='{termo}']").first.check()
            time.sleep(0.5)
        except Exception as e:
            print(f"[ERP] Aviso ao selecionar filtro '{nome_opcao}': {e}")

    def _analisar_contas_a_pagar(self, numero_nota: str) -> Dict[str, Any]:
        """Lê e analisa a tela Lançamento Contas a Pagar (NF-e) [Cad_0160]."""
        time.sleep(2)
        resultado_financeiro = {"sem_boleto": False, "divergencia": False, "diferenca": 0.0}

        texto_pagina = self.page.inner_text("body")
        if not any(k in texto_pagina for k in ["Contas a Pagar", "Cad_0160", "Total Duplicatas", "Total Nota"]):
            return resultado_financeiro

        print(f"[ERP - Financeiro] Tela de Contas a Pagar detectada. Analisando valores da NF {numero_nota}...")

        try:
            # 1. Localiza a linha exata da nota na tabela do Cad_0160 (aguardando carregamento AJAX e checando iframes)
            celulas_textos = []

            def _extrair_celulas_validas(candidato_tr) -> list:
                if candidato_tr.locator("table").count() > 0:
                    return []
                cels = candidato_tr.locator("td, div[role='cell'], div[role='gridcell']")
                return [cels.nth(k).inner_text().strip() for k in range(cels.count())]

            def _linha_parece_cad0160(textos: list) -> bool:
                if len(textos) < 6:
                    return False
                idx_n = -1
                for idx, t in enumerate(textos):
                    if numero_nota in t:
                        idx_n = idx
                        break
                if idx_n == -1:
                    return False
                for idx in range(idx_n + 1, len(textos)):
                    if re.search(r"\b\d{2}/\d{2}/\d{4}\b", textos[idx]):
                        return idx + 2 < len(textos)
                return False

            def _buscar_no_escopo(escopo) -> list:
                # Primeiro tenta dentro da tabela que contém os cabeçalhos do Cad_0160
                tabela_cad = escopo.locator("table:has-text('Total Nota'):has-text('Total Duplicatas')")
                if tabela_cad.count() > 0:
                    linhas_cad = tabela_cad.last.locator("tr")
                    for i in range(linhas_cad.count() - 1, -1, -1):
                        txt = linhas_cad.nth(i).inner_text()
                        if numero_nota in txt and "Total Nota" not in txt:
                            textos = _extrair_celulas_validas(linhas_cad.nth(i))
                            if _linha_parece_cad0160(textos):
                                return textos

                # Fallback para grid com cabeçalho separado
                linhas = escopo.locator("table tr, div[role='row']")
                for i in range(linhas.count() - 1, -1, -1):
                    txt = linhas.nth(i).inner_text()
                    if numero_nota in txt and "Total Nota" not in txt:
                        textos = _extrair_celulas_validas(linhas.nth(i))
                        if _linha_parece_cad0160(textos):
                            return textos
                return []

            # Faz até 6 tentativas (~5s extras) caso a linha esteja carregando via AJAX
            for tentativa in range(6):
                celulas_textos = _buscar_no_escopo(self.page)
                if not celulas_textos and self.page.locator("iframe").count() > 0:
                    for idx_f in range(self.page.locator("iframe").count()):
                        try:
                            celulas_textos = _buscar_no_escopo(self.page.frame_locator("iframe").nth(idx_f))
                            if celulas_textos:
                                break
                        except Exception:
                            pass
                if celulas_textos:
                    break
                if tentativa < 5:
                    time.sleep(0.8)

            if not celulas_textos:
                print(f"[ERP - Financeiro] Aviso: tabela do Cad_0160 não exibiu os valores da NF {numero_nota} a tempo (ignorado para evitar falso 'sem boleto').")
                return resultado_financeiro

            fornecedor = "Desconhecido"
            tot_nota_val = 0.0
            tot_dup_val = 0.0
            qtd_parc_val = 0

            def _converter_valor(v_str: str) -> float:
                try:
                    limpo = re.sub(r"[^\d,\.]", "", str(v_str))
                    if not limpo:
                        return 0.0
                    if "," in limpo and "." in limpo:
                        limpo = limpo.replace(".", "").replace(",", ".")
                    elif "," in limpo:
                        limpo = limpo.replace(",", ".")
                    return float(limpo.strip())
                except Exception:
                    return 0.0

            if celulas_textos:
                total_celulas = len(celulas_textos)

                # Localiza a célula do número da nota
                idx_nota = 0
                for idx, c_txt in enumerate(celulas_textos):
                    if numero_nota in c_txt:
                        idx_nota = idx
                        break

                # Localiza a Data de Emissão (DD/MM/AAAA) APÓS o número da nota
                idx_data = -1
                for idx in range(idx_nota + 1, total_celulas):
                    if re.search(r"\b\d{2}/\d{2}/\d{4}\b", celulas_textos[idx]):
                        idx_data = idx
                        break

                if idx_data != -1 and idx_data + 2 < total_celulas:
                    tot_nota_val = _converter_valor(celulas_textos[idx_data + 1])
                    tot_dup_val = _converter_valor(celulas_textos[idx_data + 2])
                    if idx_data + 3 < total_celulas:
                        try:
                            qtd_parc_val = int(re.sub(r"\D", "", celulas_textos[idx_data + 3]) or 1)
                        except Exception:
                            qtd_parc_val = 1

                for c_txt in celulas_textos[:idx_nota]:
                    if len(c_txt) > 3 and not re.search(r"^\d+$", c_txt) and not re.search(r"\d{2}/\d{2}/\d{4}", c_txt):
                        fornecedor = c_txt
                        break
                if fornecedor == "Desconhecido":
                    for c_txt in celulas_textos:
                        if len(c_txt) > 3 and not re.search(r"^\d+$", c_txt) and not re.search(r"\d{2}/\d{2}/\d{4}", c_txt):
                            fornecedor = c_txt
                            break

            print(f"[ERP - Financeiro] Fornecedor: {fornecedor}")
            print(f"[ERP - Financeiro] Total Nota: R$ {tot_nota_val:.2f} | Total Duplicatas: R$ {tot_dup_val:.2f} | Parcelas: {qtd_parc_val}")

            # 1. Regra de Sem Boleto
            if tot_dup_val <= 0.0 or qtd_parc_val == 0:
                msg = f"Nota sem boletos/duplicatas geradas no Contas a Pagar (Total Nota: R$ {tot_nota_val:.2f})"
                print(f"[ERP - Financeiro] ⚠️ {msg}")
                registrar_nota_sem_boleto(numero_nota, fornecedor, f"{tot_nota_val:.2f}", msg)
                resultado_financeiro["sem_boleto"] = True
            else:
                # 2. Regra de Boleto maior que a nota além de R$ 7,00
                diferenca = tot_dup_val - tot_nota_val
                if diferenca > 7.00:
                    msg = f"Boleto (R$ {tot_dup_val:.2f}) é maior que a nota (R$ {tot_nota_val:.2f}) em R$ {diferenca:.2f} (excede tolerância de R$ 7,00)"
                    print(f"[ERP - Financeiro] ⚠️ {msg}")
                    registrar_boleto_divergente(numero_nota, fornecedor, f"{tot_nota_val:.2f}", f"{tot_dup_val:.2f}", f"{diferenca:.2f}", msg)
                    resultado_financeiro["divergencia"] = True
                    resultado_financeiro["diferenca"] = diferenca
                else:
                    print(f"[ERP - Financeiro] ✅ Boletos em conformidade (Diferença de R$ {diferenca:.2f} dentro da tolerância de R$ 7,00).")

            return resultado_financeiro

        except Exception as e:
            print(f"[ERP - Financeiro] Aviso na extração de Contas a Pagar: {e}")
            return resultado_financeiro

    def lancar_nota(self, id_unico: str, numero_nota: str, origem_contato: str) -> Dict[str, Any]:
        """Fluxo inteligente com auto-recuperação de sessão, duplicidade e auditoria financeira."""
        self.dialog_mensagem = None
        print(f"\n[ERP] Iniciando processamento da NF: {numero_nota} (Origem: {origem_contato})...")

        # Auto-recuperação de sessão caso tenha expirado
        self.verificar_ou_recuperar_sessao()

        try:
            # 0. Garante que não há modal/iframe residual aberto e limpa filtros anteriores
            if self.page.locator("div.modal_container, iframe").count() > 0:
                self._fechar_iframe_ou_sair()
            self._limpar_filtros_tela()

            # 1. Garantir filtro 'Não Importadas'
            self._selecionar_filtro("Não Importadas")

            # 2. Filtros Avançados (só clica se o campo de nota ainda não estiver visível)
            campo_nota = self._obter_campo_nota()

            precisa_abrir_filtros = (campo_nota.count() == 0)
            if not precisa_abrir_filtros:
                try:
                    precisa_abrir_filtros = not campo_nota.first.is_visible()
                except Exception:
                    precisa_abrir_filtros = True

            if precisa_abrir_filtros:
                filtros = self.page.get_by_text("Filtros Avançados")
                if filtros.count() > 0:
                    filtros.first.click()
                    time.sleep(0.5)

            # 3. Número da nota
            campo_nota = self._obter_campo_nota()

            num_str = str(numero_nota).strip()
            campo_nota.first.click()
            campo_nota.first.fill(num_str)
            campo_nota.first.press("Enter")
            time.sleep(1.2)

            # Verifica se a nota apareceu na caixa/lista logo abaixo do campo ao pressionar Enter
            if not self._nota_apareceu_na_lista_filtro(num_str):
                time.sleep(1.2)
                if not self._nota_apareceu_na_lista_filtro(num_str):
                    try:
                        campo_nota.first.fill("")
                    except Exception:
                        pass
                    msg = f"Nota {num_str} não existe no ERP (não apareceu na lista de filtro ao digitar)."
                    return self._marcar_nota_inexistente(id_unico, num_str, origem_contato, msg)

            # 4. Consultar em 'Não Importadas'
            print(f"[ERP] Consultando nota {numero_nota} no filtro 'Não Importadas'...")
            self.page.get_by_role("button", name="Consultar [F3]").first.click()
            time.sleep(2)

            btn_submit = self.page.get_by_role("button", name="Submit")
            nota_encontrada_em_nao_importadas = (btn_submit.count() > 0)

            # Se vazia em 'Não Importadas', verifica em 'Importadas'
            if not nota_encontrada_em_nao_importadas:
                print(f"[ERP] A tabela ficou vazia em 'Não Importadas'. Verificando se já foi lançada em 'Importadas'...")

                self._selecionar_filtro("Importadas")
                self.page.get_by_role("button", name="Consultar [F3]").first.click()
                time.sleep(2)

                btn_submit_importadas = self.page.get_by_role("button", name="Submit")
                nota_encontrada_em_importadas = (btn_submit_importadas.count() > 0)

                self._selecionar_filtro("Não Importadas")

                if nota_encontrada_em_importadas:
                    msg = f"Nota {numero_nota} já consta na aba de Importadas no ERP (Duplicidade)"
                    return self._marcar_duplicidade(id_unico, numero_nota, origem_contato, msg)
                else:
                    msg = f"Nota {numero_nota} não foi localizada no sistema (nem em Não Importadas, nem em Importadas)"
                    print(f"[ERP - Alerta] {msg}")
                    atualizar_status_nota(id_unico, "erro", msg)
                    registrar_outro_erro(numero_nota, origem_contato, msg)
                    return {"status": "erro", "mensagem": msg}

            print(f"[ERP] Nota {numero_nota} localizada em 'Não Importadas'! Iniciando lançamento...")

            # 5. Selecionar nota (Submit)
            btn_submit.first.click()
            time.sleep(1)

            # 6. Importar [F2]
            print("[ERP] Clicando em Importar [F2]...")
            self.page.get_by_role("button", name="Importar [F2]").first.click()

            # Aguarda até 5s pelo carregamento do iframe interno
            for _ in range(10):
                if self.page.locator("iframe").count() > 0:
                    break
                time.sleep(0.5)

            # 7. Tratar quadro interno (iframe)
            if self.page.locator("iframe").count() > 0:
                print("[ERP] Processando quadro interno (iframe)...")
                try:
                    frame = self.page.frame_locator("iframe").first
                    btn_continuar = frame.get_by_role("button", name="Continuar [F2]")
                    if btn_continuar.count() > 0:
                        print("[ERP] Clicando em Continuar [F2] no iframe...")
                        btn_continuar.click(timeout=10000)
                        time.sleep(3)
                except Exception as err_frame:
                    print(f"[ERP] Aviso ao processar iframe: {err_frame}")

            # 8. LEITURA E VALIDAÇÃO DA TELA DE RESUMO DE IMPORTAÇÃO
            print("[ERP] Analisando tela de resumo de importação...")
            time.sleep(2)

            texto_tela = self.page.inner_text("body")
            texto_iframe = ""
            if self.page.locator("iframe").count() > 0:
                try:
                    frame = self.page.frame_locator("iframe").first
                    if frame.locator("body").count() > 0:
                        texto_iframe = frame.locator("body").inner_text(timeout=2000)
                except Exception:
                    pass

            texto_completo = f"{texto_tela}\n{texto_iframe}".upper()

            # 1. Verifica indicadores de sucesso explícito na tela de resumo (usando regex para tab/quebra de linha)
            importacao_confirmada = bool(
                re.search(r"NOTAS\s+IMPORTADAS\s*:?\s*[1-9]\d*\b", texto_completo)
                or re.search(r"TOTAL\s+(?:DE\s+)?(?:NOTAS\s+)?IMPORTADAS\s*:?\s*[1-9]\d*\b", texto_completo)
            )

            # 2. Frases reais de erro (NÃO usar "IMPORTAÇÃO ABORTADA", pois ela aparece na legenda fixa da tela!)
            frases_erro_especificas = [
                "NÃO PODE SER ENCONTRADO NO CADASTRO DE PRODUTOS",
                "NAO PODE SER ENCONTRADO NO CADASTRO DE PRODUTOS",
                "NÃO ENCONTRADO NO CADASTRO DE PRODUTOS",
                "NAO ENCONTRADO NO CADASTRO DE PRODUTOS",
            ]

            # Rejeição confirmada no resumo (0 importadas E >=1 não importadas)
            resumo_rejeitou = bool(
                re.search(r"NOTAS\s+N[ÃA]O\s+IMPORTADAS\s*:?\s*[1-9]\d*\b", texto_completo)
                and re.search(r"NOTAS\s+IMPORTADAS\s*:?\s*0\b", texto_completo)
            )

            # Só classifica como produto sem cadastro se houver erro real E a importação não tiver sido confirmada
            if (any(ind in texto_completo for ind in frases_erro_especificas) or resumo_rejeitou) and not importacao_confirmada:
                detalhe = "Produtos não encontrados no cadastro do ERP (Importação Abortada)"
                match_obs = re.search(r"(NOTA \d+\s*:\s*O ITEM [^\n\r]+)", texto_completo)
                if match_obs:
                    detalhe = match_obs.group(1).strip()
                elif "CADASTRO DE PRODUTOS" in texto_completo:
                    detalhe = "Existem itens que não foram encontrados no cadastro de produtos."

                print(f"[ERP - Identificado] {detalhe}")
                self._fechar_iframe_ou_sair()
                return self._marcar_produto_sem_cadastro(id_unico, numero_nota, origem_contato, detalhe)

            # 9. AUDITORIA FINANCEIRA DA TELA DE CONTAS A PAGAR (CAD_0160)
            res_financeiro = self._analisar_contas_a_pagar(numero_nota)

            # 10. SAÍDA FINAL
            self._fechar_iframe_ou_sair()

            print(f"[ERP - Sucesso] Nota {numero_nota} lançada com sucesso!")
            atualizar_status_nota(id_unico, "lancado")
            registrar_nota_lancada(numero_nota, origem_contato, "Lançamento concluído com sucesso")
            return {
                "status": "lancado",
                "mensagem": "Lançamento concluído com sucesso",
                "financeiro": res_financeiro,
            }

        except Exception as e:
            mensagem_erro = f"Exceção durante lançamento: {str(e)}"
            print(f"[ERP - Erro] {mensagem_erro}")
            atualizar_status_nota(id_unico, "erro", mensagem_erro)
            registrar_outro_erro(numero_nota, origem_contato, mensagem_erro)
            self._fechar_iframe_ou_sair()
            return {"status": "erro", "mensagem": mensagem_erro}
        finally:
            self._limpar_filtros_tela()

    def _obter_campo_nota(self):
        """Localiza exclusivamente o campo de texto 'Número nota fiscal' (ignorando botões)."""
        campo = self.page.get_by_role("textbox", name="Número nota fiscal")
        if campo.count() > 0:
            return campo
        campo = self.page.locator("input[placeholder*='Número nota fiscal']")
        if campo.count() > 0:
            return campo
        return self.page.locator(
            "input[type='text'][name*='nota'], input[type='text'][id*='nota']"
        )

    def _limpar_filtros_tela(self) -> None:
        """Clica no botão 'Limpar [F5]' na tela Rec_1020 para não acumular notas na caixa de filtro."""
        try:
            btn_limpar = self.page.get_by_role("button", name="Limpar [F5]")
            if btn_limpar.count() == 0:
                btn_limpar = self.page.locator("button:has-text('Limpar [F5]'), input[value*='Limpar']")
            if btn_limpar.count() > 0 and btn_limpar.first.is_visible():
                btn_limpar.first.click(timeout=3000)
                time.sleep(0.8)
        except Exception:
            pass

    def _fechar_iframe_ou_sair(self) -> None:
        """Fecha qualquer modal/iframe aberto (Sair, Fechar, Voltar, X ou Esc)."""
        try:
            seletores_fechar = (
                "button:has-text('Sair'), input[type='button'][value*='Sair'], "
                "button:has-text('Fechar'), input[type='button'][value*='Fechar'], "
                "button:has-text('Voltar'), input[type='button'][value*='Voltar'], "
                "button:has-text('Cancelar'), input[type='button'][value*='Cancelar'], "
                "button:has-text('OK'), input[type='button'][value='OK']"
            )

            # 1. Tenta fechar por dentro de todos os iframes abertos (rolando até o rodapé primeiro)
            total_iframes = self.page.locator("iframe").count()
            for idx in range(total_iframes - 1, -1, -1):
                try:
                    frame = self.page.frame_locator("iframe").nth(idx)
                    try:
                        frame.locator("body").evaluate("el => window.scrollTo(0, document.body.scrollHeight)")
                    except Exception:
                        pass

                    btn_frame = frame.locator(seletores_fechar)
                    if btn_frame.count() > 0:
                        alvo_btn = btn_frame.last
                        alvo_btn.scroll_into_view_if_needed()
                        alvo_btn.click(timeout=3000)
                        time.sleep(0.8)

                    # Se o modal ainda estiver aberto, tenta Escape diretamente dentro do iframe
                    if self.page.locator("div.modal_container, iframe").count() > 0:
                        try:
                            frame.locator("body").press("Escape")
                            time.sleep(0.6)
                        except Exception:
                            pass
                except Exception:
                    pass

            # 2. Tenta fechar pelo botão do container do modal (ex: X ou fechar)
            if self.page.locator("div.modal_container, iframe").count() > 0:
                try:
                    btn_modal = self.page.locator(
                        "div.modal_container button:has-text('Sair'), div.modal_container button:has-text('Fechar'), "
                        "div.modal_container input[value*='Sair'], div.modal_container input[value*='Fechar'], "
                        "div.modal_container .modal_close, div.modal_container a[title*='Fechar'], "
                        "div.modal_container span[title*='Fechar']"
                    )
                    if btn_modal.count() > 0:
                        btn_modal.last.scroll_into_view_if_needed()
                        btn_modal.last.click(timeout=3000)
                        time.sleep(0.8)
                except Exception:
                    pass

            # 3. Tenta botão 'Sair [Esc]' na própria página
            if self.page.locator("div.modal_container, iframe").count() > 0 or self.page.get_by_role("button", name="Sair [Esc]").count() > 0:
                try:
                    btn_sair = self.page.get_by_role("button", name="Sair [Esc]")
                    if btn_sair.count() > 0 and btn_sair.first.is_visible():
                        btn_sair.first.click(timeout=3000)
                        time.sleep(0.8)
                except Exception:
                    pass

            # 4. Último recurso: tecla Escape na página ou fechar overlay modal_container se travado
            if self.page.locator("div.modal_container, iframe").count() > 0:
                self.page.keyboard.press("Escape")
                time.sleep(0.6)

            if self.page.locator("div.modal_container").count() > 0:
                try:
                    botoes_debug = self.page.evaluate("""() => {
                        const res = [];
                        document.querySelectorAll('iframe').forEach(ifr => {
                            try {
                                const doc = ifr.contentDocument;
                                if (doc) {
                                    doc.querySelectorAll('button, input[type=button], input[type=submit]').forEach(b => {
                                        res.push((b.innerText || b.value || '').trim());
                                    });
                                }
                            } catch (e) {}
                        });
                        return res.filter(Boolean);
                    }""")
                    if botoes_debug:
                        print(f"[ERP - Aviso] Modal ainda aberto. Botões encontrados no iframe: {botoes_debug}")
                except Exception:
                    pass
        except Exception:
            pass

    def _nota_apareceu_na_lista_filtro(self, numero_nota: str) -> bool:
        """
        Verifica se após digitar a nota e pressionar Enter, ela foi adicionada
        na caixa/lista de seleção logo abaixo do campo 'Número nota fiscal' (ex: '1 - 7484959').
        """
        try:
            num_esc = re.escape(str(numero_nota).strip())
            padrao = re.compile(rf"(?:^|\s|-){num_esc}(?:\s|$)")

            # 1. Verifica opções de <select> (comum no SGFPOD/WebSpeed)
            opcoes = self.page.locator("select option, [role='option'], [role='listbox'] li")
            for i in range(opcoes.count()):
                txt = opcoes.nth(i).inner_text().strip()
                if padrao.search(txt):
                    return True

            # 2. Fallback caso o componente use uma div/lista customizada com o padrão "1 - <nota>"
            itens_custom = self.page.get_by_text(re.compile(rf"^\s*\d+\s*-\s*{num_esc}\s*$"))
            if itens_custom.count() > 0:
                return True

            return False
        except Exception:
            # Em caso de falha na leitura do DOM, não bloqueia o fluxo normal
            return True

    def _marcar_duplicidade(self, id_unico: str, numero_nota: str, origem: str, msg: str) -> Dict[str, Any]:
        print(f"[ERP - Duplicidade] {msg}")
        atualizar_status_nota(id_unico, "duplicada_erp", msg)
        registrar_duplicidade(numero_nota, origem, msg)
        return {"status": "duplicada_erp", "mensagem": msg}

    def _marcar_produto_sem_cadastro(self, id_unico: str, numero_nota: str, origem: str, msg: str) -> Dict[str, Any]:
        print(f"[ERP - Produto Sem Cadastro] {msg}")
        atualizar_status_nota(id_unico, "produto_sem_cadastro", msg)
        registrar_produto_sem_cadastro(numero_nota, origem, msg)
        return {"status": "produto_sem_cadastro", "mensagem": msg}

    def _marcar_nota_inexistente(self, id_unico: str, numero_nota: str, origem: str, msg: str) -> Dict[str, Any]:
        print(f"[ERP - Nota Inexistente] {msg}")
        atualizar_status_nota(id_unico, "nota_inexistente", msg)
        registrar_nota_inexistente(numero_nota, origem, msg)
        return {"status": "nota_inexistente", "mensagem": msg}

    def fechar(self) -> None:
        try:
            if self.context:
                self.context.close()
            if self._playwright and not self._external_playwright:
                self._playwright.stop()
        except Exception:
            pass
