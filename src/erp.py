import re
import time
from datetime import datetime
from typing import Dict, Any, Optional
from playwright.sync_api import sync_playwright, BrowserContext, Page, Dialog
from .config import (
    ERP_SESSION_DIR,
    ERP_URL,
    ERP_USUARIO,
    ERP_SENHA,
    ERROS_PRINTS_DIR,
    MODO_TESTE,
)
from .database import atualizar_status_nota, registrar_falha_lancamento
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
        resultado_financeiro = {
            "tela_financeira_aberta": False,
            "sem_boleto": False,
            "divergencia": False,
            "diferenca": 0.0,
        }

        texto_pagina = self.page.inner_text("body")
        indicadores_cad0160 = [
            "Lançamento Contas a Pagar",
            "Cad_0160]",
            "Total Duplicatas",
            "Com Contas a Pagar",
            "Sem Contas a Pagar",
        ]
        tela_financeira_aberta = any(k in texto_pagina for k in indicadores_cad0160)
        if not tela_financeira_aberta and self.page.locator("iframe").count() > 0:
            for idx_f in range(self.page.locator("iframe").count()):
                try:
                    txt_f = self.page.frame_locator("iframe").nth(idx_f).locator("body").inner_text(timeout=1500)
                    if any(k in txt_f for k in indicadores_cad0160):
                        tela_financeira_aberta = True
                        break
                except Exception:
                    pass

        if not tela_financeira_aberta:
            return resultado_financeiro

        resultado_financeiro["tela_financeira_aberta"] = True

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

            # Faz até 10 tentativas (~8s extras) caso a linha esteja carregando via AJAX
            for tentativa in range(10):
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
                if tentativa < 9:
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
        if not self.verificar_ou_recuperar_sessao():
            msg = f"ERP indisponível no momento; NF {numero_nota} mantida na fila para próxima tentativa."
            print(f"[ERP - Adiado] {msg}")
            return {"status": "adiado", "mensagem": msg}

        try:
            # 0. Garante que não há modal/iframe residual aberto e limpa filtros anteriores
            if self.page.locator("div.modal_container, iframe").count() > 0:
                self._fechar_iframe_ou_sair()
            if self.page.locator("div.modal_container").count() > 0:
                print("[ERP - Auto-Recuperação] Modal persistente detectado antes da nota. Recarregando tela Rec_1020...")
                try:
                    self.page.reload(wait_until="load", timeout=30000)
                    time.sleep(1.5)
                    self.verificar_ou_recuperar_sessao()
                except Exception as err_rec:
                    print(f"[ERP - Auto-Recuperação] Aviso ao recarregar tela: {err_rec}")
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
                    novo_status, tentativa_atual = registrar_falha_lancamento(
                        id_unico, msg, max_tentativas=2
                    )
                    if novo_status == "aguardando_retry":
                        print(
                            f"[ERP - Nova Tentativa {tentativa_atual}/2] Nota {num_str} não apareceu no filtro; "
                            f"nova checagem automática em ~30s."
                        )
                        return {"status": "aguardando_retry", "mensagem": msg}
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
                    novo_status, tentativa_atual = registrar_falha_lancamento(
                        id_unico, msg, max_tentativas=3
                    )
                    if novo_status == "aguardando_retry":
                        print(
                            f"[ERP - Nova Tentativa {tentativa_atual}/3] {msg}; "
                            f"nova tentativa automática em ~30s."
                        )
                    else:
                        print(f"[ERP - Erro Definitivo ({tentativa_atual}/3)] {msg}")
                        self._salvar_screenshot_erro(numero_nota)
                        registrar_outro_erro(numero_nota, origem_contato, msg)
                    return {"status": novo_status, "mensagem": msg}

            print(f"[ERP] Nota {numero_nota} localizada em 'Não Importadas'! Iniciando lançamento...")

            # 5. Selecionar nota (Submit)
            btn_submit.first.click()
            time.sleep(1)

            # 6. Importar [F2]
            print("[ERP] Clicando em Importar [F2]...")
            self.page.get_by_role("button", name="Importar [F2]").first.click()

            # Aguarda até 8s pelo carregamento do iframe interno
            for _ in range(16):
                if self.page.locator("iframe").count() > 0:
                    break
                time.sleep(0.5)

            # 7. Tratar quadro interno (iframe) - lê resumo antes e depois de Continuar [F2]
            if self.page.locator("iframe").count() > 0:
                print("[ERP] Processando quadro interno (iframe)...")
                try:
                    for idx_f in range(self.page.locator("iframe").count() - 1, -1, -1):
                        frame = self.page.frame_locator("iframe").nth(idx_f)
                        btn_continuar = frame.get_by_role("button", name="Continuar [F2]")
                        if btn_continuar.count() > 0 and btn_continuar.first.is_visible():
                            print("[ERP] Clicando em Continuar [F2] no iframe...")
                            btn_continuar.first.click(timeout=10000)
                            time.sleep(2)
                            break
                except Exception as err_frame:
                    print(f"[ERP] Aviso ao processar iframe: {err_frame}")

            # 8. LEITURA E VALIDAÇÃO DA TELA DE RESUMO DE IMPORTAÇÃO (aguarda confirmação real ou rejeição)
            print("[ERP] Analisando tela de resumo de importação...")

            frases_erro_especificas = [
                "NÃO PODE SER ENCONTRADO NO CADASTRO DE PRODUTOS",
                "NAO PODE SER ENCONTRADO NO CADASTRO DE PRODUTOS",
                "NÃO ENCONTRADO NO CADASTRO DE PRODUTOS",
                "NAO ENCONTRADO NO CADASTRO DE PRODUTOS",
            ]
            indicadores_cad0160 = (
                "LANÇAMENTO CONTAS A PAGAR",
                "LANCAMENTO CONTAS A PAGAR",
                "CAD_0160]",
                "TOTAL DUPLICATAS",
                "COM CONTAS A PAGAR",
                "SEM CONTAS A PAGAR",
            )

            texto_completo = ""
            importacao_confirmada = False
            cad0160_detectado = False
            resumo_rejeitou = False
            erro_produto_detectado = False

            for tentativa_tela in range(10):
                texto_completo = self._coletar_textos_tela_e_iframes().upper()
                importacao_confirmada = bool(
                    re.search(r"NOTAS\s+IMPORTADAS\s*:?\s*[1-9]\d*\b", texto_completo)
                    or re.search(r"TOTAL\s+(?:DE\s+)?(?:NOTAS\s+)?IMPORTADAS\s*:?\s*[1-9]\d*\b", texto_completo)
                )
                cad0160_detectado = any(ind in texto_completo for ind in indicadores_cad0160)
                resumo_rejeitou = bool(
                    re.search(r"NOTAS\s+N[ÃA]O\s+IMPORTADAS\s*:?\s*[1-9]\d*\b", texto_completo)
                    and re.search(r"NOTAS\s+IMPORTADAS\s*:?\s*0\b", texto_completo)
                )
                erro_produto_detectado = any(ind in texto_completo for ind in frases_erro_especificas)

                if importacao_confirmada or cad0160_detectado or erro_produto_detectado or resumo_rejeitou:
                    break
                if tentativa_tela < 9:
                    time.sleep(0.8)

            # Produto sem cadastro confirmado
            if (erro_produto_detectado or (resumo_rejeitou and "CADASTRO DE PRODUTOS" in texto_completo)) and not importacao_confirmada:
                detalhe = "Produtos não encontrados no cadastro do ERP (Importação Abortada)"
                match_obs = re.search(r"(NOTA \d+\s*:\s*O ITEM [^\n\r]+)", texto_completo)
                if match_obs:
                    detalhe = match_obs.group(1).strip()
                elif "CADASTRO DE PRODUTOS" in texto_completo:
                    detalhe = "Existem itens que não foram encontrados no cadastro de produtos."

                print(f"[ERP - Identificado] {detalhe}")
                self._fechar_iframe_ou_sair()
                return self._marcar_produto_sem_cadastro(id_unico, numero_nota, origem_contato, detalhe)

            # Outra rejeição explícita no resumo (Notas não importadas >= 1 e Notas importadas == 0 sem ser cadastro)
            if resumo_rejeitou and not importacao_confirmada:
                obs_match = re.search(r"OBSERVA[ÇC][ÕO]ES DA NOTA SELECIONADA\s*([\s\S]{1,220})", texto_completo)
                motivo_resumo = (
                    " ".join(obs_match.group(1).split())[:180]
                    if obs_match
                    else "Resumo de importação retornou Notas importadas = 0"
                )
                print(f"[ERP - Importação Recusada] {motivo_resumo}")
                self._salvar_screenshot_erro(numero_nota)
                self._fechar_iframe_ou_sair()
                novo_status, tentativa_atual = registrar_falha_lancamento(
                    id_unico, motivo_resumo, max_tentativas=3
                )
                if novo_status == "erro":
                    registrar_outro_erro(numero_nota, origem_contato, motivo_resumo)
                return {"status": novo_status, "mensagem": motivo_resumo}

            # 9. AUDITORIA FINANCEIRA DA TELA DE CONTAS A PAGAR (CAD_0160)
            res_financeiro = self._analisar_contas_a_pagar(numero_nota)

            # 10. PROVA OBRIGATÓRIA DE LANÇAMENTO (anti-falso-positivo)
            confirmada_diretamente = (
                importacao_confirmada
                or cad0160_detectado
                or res_financeiro.get("tela_financeira_aberta", False)
            )
            if not confirmada_diretamente:
                print(
                    f"[ERP - Verificação] Importação da NF {numero_nota} sem confirmação visual direta. "
                    f"Validando status real no ERP..."
                )
                self._salvar_screenshot_erro(numero_nota)
                esta_importada = self._confirmar_nota_importada_no_erp(numero_nota)
                if esta_importada is not True:
                    detalhe_dialog = f" | Alerta: {self.dialog_mensagem}" if self.dialog_mensagem else ""
                    resumo_tela_curto = " ".join(texto_completo.split())[:160]
                    motivo_nao_lanc = (
                        f"NF {numero_nota} não foi confirmada como Importada no ERP"
                        f"{detalhe_dialog}. Tela: {resumo_tela_curto}"
                    )
                    print(f"[ERP - Bloqueado Anti-Falso-Positivo] {motivo_nao_lanc}")
                    novo_status, tentativa_atual = registrar_falha_lancamento(
                        id_unico, motivo_nao_lanc, max_tentativas=3
                    )
                    if novo_status == "aguardando_retry":
                        print(
                            f"[ERP - Nova Tentativa {tentativa_atual}/3] NF {numero_nota} retida antes de concluir; "
                            f"nova tentativa automática em ~30s."
                        )
                    else:
                        print(f"[ERP - Erro Definitivo ({tentativa_atual}/3)] {motivo_nao_lanc}")
                        registrar_outro_erro(numero_nota, origem_contato, motivo_nao_lanc)
                    return {"status": novo_status, "mensagem": motivo_nao_lanc}

            # 11. SAÍDA FINAL
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
            novo_status, tentativa_atual = registrar_falha_lancamento(
                id_unico, mensagem_erro, max_tentativas=3
            )
            if novo_status == "aguardando_retry":
                print(
                    f"[ERP - Nova Tentativa {tentativa_atual}/3] Falha temporária na NF {numero_nota}; "
                    f"nova tentativa automática em ~30s."
                )
            else:
                print(
                    f"[ERP - Erro Definitivo ({tentativa_atual}/3)] NF {numero_nota} falhou após 3 tentativas."
                )
                self._salvar_screenshot_erro(numero_nota)
                registrar_outro_erro(numero_nota, origem_contato, mensagem_erro)

            self._fechar_iframe_ou_sair()
            try:
                if self.page and self.page.locator("div.modal_container, iframe").count() > 0:
                    print("[ERP] Modal/iframe persistente após falha; recarregando tela do ERP...")
                    self.page.reload(wait_until="load", timeout=30000)
                    time.sleep(1.2)
                    self.verificar_ou_recuperar_sessao()
            except Exception as rec_err:
                print(f"[ERP - Aviso] Falha ao resetar tela pós-erro: {rec_err}")

            return {"status": novo_status, "mensagem": mensagem_erro}
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
        """Fecha qualquer modal/iframe aberto (Sair, Fechar, Voltar, Cancelar, OK ou Esc)."""
        try:
            seletores_prioritarios = (
                "button:has-text('Sair'), input[type='button'][value*='Sair'], "
                "button:has-text('Fechar'), input[type='button'][value*='Fechar'], "
                "button:has-text('Close'), button[aria-label='Close']"
            )
            seletores_secundarios = (
                "button:has-text('Voltar'), input[type='button'][value*='Voltar'], "
                "button:has-text('Cancelar'), input[type='button'][value*='Cancelar'], "
                "button:has-text('OK'), input[type='button'][value='OK']"
            )

            def _clicar_visiveis_no_escopo(escopo, seletor: str) -> bool:
                """Tenta cada botão realmente visível (bounding_box > 0) até o modal sumir."""
                btns = escopo.locator(seletor)
                for b_idx in range(btns.count()):
                    if self.page.locator("div.modal_container, iframe").count() == 0:
                        return True
                    try:
                        cand = btns.nth(b_idx)
                        if not cand.is_visible():
                            continue
                        cand.scroll_into_view_if_needed()
                        box = cand.bounding_box()
                        if not box or box["width"] <= 0 or box["height"] <= 0:
                            continue
                        cand.click(timeout=3000)
                        time.sleep(0.8)
                        if self.page.locator("div.modal_container, iframe").count() == 0:
                            return True
                    except Exception:
                        continue
                return self.page.locator("div.modal_container, iframe").count() == 0

            # Executa até 2 rodadas para fechar modais empilhados (ex: Contas a Pagar + Resumo)
            for _ in range(2):
                if self.page.locator("div.modal_container, iframe").count() == 0 and self.page.get_by_role("button", name="Sair [Esc]").count() == 0:
                    break

                # 1. Tenta fechar por dentro de todos os iframes abertos
                total_iframes = self.page.locator("iframe").count()
                for idx in range(total_iframes - 1, -1, -1):
                    try:
                        frame = self.page.frame_locator("iframe").nth(idx)
                        try:
                            frame.locator("body").evaluate("el => window.scrollTo(0, document.body.scrollHeight)")
                        except Exception:
                            pass

                        if _clicar_visiveis_no_escopo(frame, seletores_prioritarios):
                            continue
                        if _clicar_visiveis_no_escopo(frame, seletores_secundarios):
                            continue

                        # Se o modal ainda estiver aberto, tenta Escape diretamente dentro do iframe
                        if self.page.locator("div.modal_container, iframe").count() > 0:
                            try:
                                frame.locator("body").press("Escape")
                                time.sleep(0.6)
                            except Exception:
                                pass
                    except Exception:
                        pass

                # 2. Tenta fechar pelo botão visível do container do modal
                if self.page.locator("div.modal_container, iframe").count() > 0:
                    _clicar_visiveis_no_escopo(
                        self.page,
                        "div.modal_container button:has-text('Sair'), div.modal_container button:has-text('Fechar'), "
                        "div.modal_container button:has-text('Close'), div.modal_container button[aria-label='Close'], "
                        "div.modal_container button:has-text('×'), "
                        "div.modal_container input[value*='Sair'], div.modal_container input[value*='Fechar'], "
                        "div.modal_container .modal_close, div.modal_container a[title*='Fechar'], "
                        "div.modal_container span[title*='Fechar']",
                    )

                # 3. Tenta botão 'Sair [Esc]' na própria página
                if self.page.locator("div.modal_container, iframe").count() > 0 or self.page.get_by_role("button", name="Sair [Esc]").count() > 0:
                    try:
                        btn_sair = self.page.get_by_role("button", name="Sair [Esc]")
                        for s_idx in range(btn_sair.count()):
                            try:
                                cand_s = btn_sair.nth(s_idx)
                                if cand_s.is_visible():
                                    cand_s.click(timeout=3000)
                                    time.sleep(0.8)
                                    if self.page.locator("div.modal_container, iframe").count() == 0:
                                        break
                            except Exception:
                                continue
                    except Exception:
                        pass

                # 4. Último recurso: tecla Escape na página se o modal ainda persistir
                if self.page.locator("div.modal_container, iframe").count() > 0:
                    self.page.keyboard.press("Escape")
                    time.sleep(0.6)

            if self.page.locator("div.modal_container").count() > 0:
                try:
                    botoes_visiveis = self.page.evaluate("""() => {
                        const res = [];
                        document.querySelectorAll('iframe').forEach(ifr => {
                            try {
                                const doc = ifr.contentDocument;
                                if (doc) {
                                    doc.querySelectorAll('button, input[type=button], input[type=submit]').forEach(b => {
                                        const r = b.getBoundingClientRect();
                                        if (r.width > 0 && r.height > 0) {
                                            res.push((b.innerText || b.value || '').trim());
                                        }
                                    });
                                }
                            } catch (e) {}
                        });
                        return res.filter(Boolean);
                    }""")
                    if botoes_visiveis:
                        print(f"[ERP - Aviso] Modal ainda aberto. Botões visíveis no iframe: {botoes_visiveis}")
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

    def _coletar_textos_tela_e_iframes(self) -> str:
        """Agrega texto visível do body principal, todos os iframes abertos e último alerta nativo."""
        partes = []
        try:
            partes.append(self.page.inner_text("body"))
        except Exception:
            pass
        try:
            total_iframes = self.page.locator("iframe").count()
            for idx_f in range(total_iframes):
                try:
                    frame = self.page.frame_locator("iframe").nth(idx_f)
                    if frame.locator("body").count() > 0:
                        partes.append(frame.locator("body").inner_text(timeout=1500))
                except Exception:
                    continue
        except Exception:
            pass
        if self.dialog_mensagem:
            partes.append(f"ALERTA_DIALOG: {self.dialog_mensagem}")
        return "\n".join(partes)

    def _confirmar_nota_importada_no_erp(self, numero_nota: str) -> Optional[bool]:
        """
        Verifica diretamente na grade do ERP se a NF realmente mudou para 'Importadas'.
        Retorna True se estiver em Importadas, False se continuar em Não Importadas, ou None se incerto.
        """
        try:
            self._fechar_iframe_ou_sair()
            if self.page.locator("div.modal_container, iframe").count() > 0:
                self.page.reload(wait_until="load", timeout=30000)
                time.sleep(1.5)
                self.verificar_ou_recuperar_sessao()

            self._limpar_filtros_tela()
            campo_nota = self._obter_campo_nota()
            if campo_nota.count() == 0 or not campo_nota.first.is_visible():
                filtros = self.page.get_by_text("Filtros Avançados")
                if filtros.count() > 0:
                    filtros.first.click()
                    time.sleep(0.5)
                campo_nota = self._obter_campo_nota()

            num_str = str(numero_nota).strip()
            if campo_nota.count() > 0:
                campo_nota.first.click()
                campo_nota.first.fill(num_str)
                campo_nota.first.press("Enter")
                time.sleep(1.0)

            self._selecionar_filtro("Importadas")
            self.page.get_by_role("button", name="Consultar [F3]").first.click()
            time.sleep(1.8)
            em_importadas = self.page.get_by_role("button", name="Submit").count() > 0
            if em_importadas:
                self._limpar_filtros_tela()
                self._selecionar_filtro("Não Importadas")
                return True

            self._selecionar_filtro("Não Importadas")
            self.page.get_by_role("button", name="Consultar [F3]").first.click()
            time.sleep(1.8)
            em_nao_importadas = self.page.get_by_role("button", name="Submit").count() > 0
            self._limpar_filtros_tela()

            if em_nao_importadas:
                return False
            return None
        except Exception as err_ver:
            print(f"[ERP - Aviso] Falha ao reconsultar status real da NF {numero_nota}: {err_ver}")
            return None

    def _salvar_screenshot_erro(self, numero_nota: str, max_prints: int = 50) -> None:
        """Captura screenshot da tela do ERP no erro definitivo para facilitar diagnóstico."""
        try:
            if not self.page or self.page.is_closed():
                return
            ERROS_PRINTS_DIR.mkdir(parents=True, exist_ok=True)
            num_limpo = re.sub(r"[^\w-]", "_", str(numero_nota))
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            caminho = ERROS_PRINTS_DIR / f"nf_{num_limpo}_{ts}.png"
            self.page.screenshot(path=str(caminho), full_page=False)
            print(f"[ERP - Evidência] Print do erro salvo em: reports/erros_prints/{caminho.name}")

            prints = sorted(
                ERROS_PRINTS_DIR.glob("nf_*.png"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for antigo in prints[max_prints:]:
                try:
                    antigo.unlink()
                except Exception:
                    pass
        except Exception as err_print:
            print(f"[ERP - Aviso] Não foi possível salvar print do erro: {err_print}")

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
