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
    ):
        if headless is None:
            self.headless = not MODO_TESTE
        else:
            self.headless = headless

        self.url = (url or ERP_URL).strip()
        self.usuario = (usuario or ERP_USUARIO).strip()
        self.senha = (senha or ERP_SENHA).strip()

        self._playwright = None
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
        self._playwright = sync_playwright().start()
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--disable-infobars",
        ]
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(ERP_SESSION_DIR),
            headless=self.headless,
            viewport={"width": 1366, "height": 850},
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
            url_atual = self.page.url.lower()
            # Se voltou para a tela de login ou campos de usuário estão visíveis
            precisa_login = (
                "login.pod" in url_atual
                or self.page.get_by_role("textbox", name="Usuário").count() > 0
                or self.page.get_by_role("textbox", name="Usuário*").count() > 0
            )
            if precisa_login:
                print("[ERP - Auto-Recuperação] 🔄 Sessão expirada detectada! Reconectando ao ERP automaticamente...")
                return self.login_e_navegar_para_entrada()
            return True
        except Exception as e:
            print(f"[ERP - Auto-Recuperação] Erro ao verificar sessão: {e}. Refazendo login...")
            return self.login_e_navegar_para_entrada()

    def login_e_navegar_para_entrada(self) -> bool:
        """Executa o login no ERP e navega até a tela de Entrada de Mercadorias NF-e."""
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
            linhas = self.page.locator("table tr, div[role='row']")
            linha_nota = None

            for i in range(linhas.count()):
                txt = linhas.nth(i).inner_text()
                if numero_nota in txt:
                    linha_nota = linhas.nth(i)
                    break

            if not linha_nota:
                for i in range(linhas.count()):
                    txt = linhas.nth(i).inner_text()
                    if "Total Nota" not in txt and any(c.isdigit() for c in txt):
                        linha_nota = linhas.nth(i)
                        break

            fornecedor = "Desconhecido"
            tot_nota_val = 0.0
            tot_dup_val = 0.0
            qtd_parc_val = 0

            if linha_nota:
                celulas = linha_nota.locator("td")
                if celulas.count() >= 8:
                    fornecedor = celulas.nth(1).inner_text().strip()
                    tot_nota_str = celulas.nth(5).inner_text().strip()
                    tot_dup_str = celulas.nth(6).inner_text().strip()
                    qtd_parc_str = celulas.nth(7).inner_text().strip()
                else:
                    txt = linha_nota.inner_text()
                    valores = re.findall(r"\b\d+[\.,]\d{2}\b", txt)
                    tot_nota_str = valores[0] if len(valores) > 0 else "0,00"
                    tot_dup_str = valores[1] if len(valores) > 1 else "0,00"
                    qtd_parc_str = "0"

                def _converter_valor(v_str: str) -> float:
                    try:
                        return float(v_str.replace(".", "").replace(",", ".").strip())
                    except Exception:
                        return 0.0

                tot_nota_val = _converter_valor(tot_nota_str)
                tot_dup_val = _converter_valor(tot_dup_str)
                try:
                    qtd_parc_val = int(re.sub(r"\D", "", qtd_parc_str) or 0)
                except Exception:
                    qtd_parc_val = 0

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
            # 1. Garantir filtro 'Não Importadas'
            self._selecionar_filtro("Não Importadas")

            # 2. Filtros Avançados
            filtros = self.page.get_by_text("Filtros Avançados")
            if filtros.count() > 0:
                filtros.first.click()
                time.sleep(0.5)

            # 3. Número da nota
            campo_nota = self.page.get_by_role("textbox", name="Número nota fiscal")
            if campo_nota.count() == 0:
                campo_nota = self.page.locator("input[name*='nota'], input[id*='nota']")

            campo_nota.first.click()
            campo_nota.first.fill(str(numero_nota).strip())
            campo_nota.first.press("Enter")
            time.sleep(0.5)

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
            time.sleep(3)

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

            indicadores_sem_cadastro = [
                "NÃO PODE SER ENCONTRADO NO CADASTRO DE PRODUTOS",
                "NAO PODE SER ENCONTRADO NO CADASTRO DE PRODUTOS",
                "CADASTRO DE PRODUTOS",
                "NOTAS NÃO IMPORTADAS 1",
                "NOTAS NAO IMPORTADAS 1",
                "IMPORTAÇÃO ABORTADA",
                "IMPORTACAO ABORTADA",
                "DIVERGÊNCIA",
                "DIVERGENCIA",
                "NENHUM PRODUTO ATIVADO",
            ]

            if any(ind in texto_completo for ind in indicadores_sem_cadastro):
                detalhe = "Produtos não encontrados no cadastro do ERP (Importação Abortada / Divergência)"
                match_obs = re.search(r"(NOTA \d+\s*:\s*O ITEM [^\n\r]+)", texto_completo)
                if match_obs:
                    detalhe = match_obs.group(1).strip()
                elif "NÃO PODE SER ENCONTRADO NO CADASTRO DE PRODUTOS" in texto_completo:
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

    def _fechar_iframe_ou_sair(self) -> None:
        try:
            if self.page.locator("iframe").count() > 0:
                try:
                    frame = self.page.frame_locator("iframe").first
                    btn_sair_frame = frame.get_by_role("button", name="Sair [Esc]")
                    if btn_sair_frame.count() > 0:
                        btn_sair_frame.click(timeout=3000)
                        time.sleep(1)
                except Exception:
                    pass

            btn_sair = self.page.get_by_role("button", name="Sair [Esc]")
            if btn_sair.count() > 0:
                btn_sair.first.click(timeout=3000)
                time.sleep(1)
        except Exception:
            pass

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

    def fechar(self) -> None:
        try:
            if self.context:
                self.context.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
