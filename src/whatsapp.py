import json
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Tuple, List, Optional, Dict, Any
from playwright.sync_api import sync_playwright, BrowserContext, Page
from .config import (
    WHATSAPP_SESSION_DIR,
    WHATSAPP_CHAT_ALVO,
    WHATSAPP_IGNORAR,
    MODO_TESTE,
    DATA_DIR,
)
from .extractor import extrair_numeros_notas
from .database import inserir_nota, obter_status_nota
from .reporter import registrar_duplicidade

CHECKPOINT_FILE = DATA_DIR / "whatsapp_checkpoint.json"


class WhatsAppMonitor:
    def __init__(
        self,
        headless: Optional[bool] = None,
        playwright_instance=None,
        chats_ignorar: Optional[str] = None,
        chat_alvo: Optional[str] = None,
    ):
        if headless is None:
            self.headless = not MODO_TESTE
        else:
            self.headless = headless

        self._external_playwright = playwright_instance is not None
        self._playwright = playwright_instance
        self.chats_ignorar = WHATSAPP_IGNORAR if chats_ignorar is None else chats_ignorar
        self.chat_alvo = WHATSAPP_CHAT_ALVO if chat_alvo is None else chat_alvo.strip()
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def _obter_lista_ignorados(self) -> List[str]:
        """Retorna a lista normalizada de nomes de grupos/chats a ignorar."""
        raw = (self.chats_ignorar or "").replace(";", ",")
        return [item.strip().lower() for item in raw.split(",") if item.strip()]

    def _chat_esta_ignorado(self, nome_chat: str) -> bool:
        """Verifica se o chat/grupo corresponde a algum item da lista de ignorados."""
        if not nome_chat:
            return False
        nome_norm = nome_chat.strip().lower()
        for termo in self._obter_lista_ignorados():
            if termo in nome_norm:
                return True
        return False

    def __enter__(self):
        self.iniciar()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.fechar()

    def iniciar(self) -> None:
        """Inicializa o navegador com perfil persistente para manter a sessão salva."""
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
            user_data_dir=str(WHATSAPP_SESSION_DIR),
            headless=self.headless,
            viewport={"width": 1280, "height": 800},
            user_agent=user_agent_desktop,
            locale="pt-BR",
            args=args,
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()

    def abrir_e_garantir_login(self, timeout_login: int = 120) -> bool:
        """Acessa o WhatsApp Web e aguarda autenticação."""
        print("[WhatsApp] Acessando web.whatsapp.com...")
        self.page.goto("https://web.whatsapp.com", wait_until="load")

        seletor_painel_conversas = "#pane-side, div[aria-label='Lista de conversas'], div[role='grid']"
        seletor_qrcode = "canvas[aria-label='Scan me!'], div[data-ref]"

        tempo_inicial = time.time()
        ultimo_aviso = tempo_inicial
        print("[WhatsApp] Verificando sessão...")

        while time.time() - tempo_inicial < timeout_login:
            if self.page.locator(seletor_painel_conversas).count() > 0:
                print("[WhatsApp] Sessão autenticada e pronta para uso!")
                self._fechar_modais_whatsapp()
                return True

            if self.page.locator(seletor_qrcode).count() > 0:
                if self.headless:
                    print("[WhatsApp] ⚠️ QR Code necessário! Marque 'Modo Teste (Navegador Visível)' para escanear o QR Code.")
                else:
                    print("[WhatsApp] Aguardando escaneamento do QR Code no navegador aberto...")
                time.sleep(3)
                continue

            agora = time.time()
            if agora - ultimo_aviso >= 15:
                ultimo_aviso = agora
                try:
                    txt_body = self.page.inner_text("body")[:200].replace("\n", " ").strip()
                    if "google chrome" in txt_body.lower() or "atualize" in txt_body.lower():
                        print(f"[WhatsApp] ⚠️ Aviso do navegador: {txt_body}")
                    else:
                        print("[WhatsApp] Ainda carregando WhatsApp Web em segundo plano...")
                except Exception:
                    print("[WhatsApp] Ainda carregando WhatsApp Web em segundo plano...")

            time.sleep(1)

        print("[WhatsApp] Tempo limite excedido para autenticação no WhatsApp Web.")
        return False

    def _fechar_modais_whatsapp(self) -> None:
        """Fecha popups/modais do WhatsApp Web (ex: 'Continuar', 'Novidades') que bloqueiam cliques."""
        try:
            modal = self.page.locator("div[role='dialog'][aria-modal='true']")
            if modal.count() == 0:
                return

            print("[WhatsApp] Popup/modal detectado na tela. Tentando fechar automaticamente...")
            botoes_dispensa = modal.first.locator(
                "button:has-text('Continuar'), button:has-text('OK'), button:has-text('Ok'), "
                "button:has-text('Entendi'), button:has-text('Fechar'), button:has-text('Agora não'), "
                "button[aria-label='Fechar'], button[aria-label='Close']"
            )
            if botoes_dispensa.count() > 0:
                botoes_dispensa.first.click(timeout=3000)
                time.sleep(0.8)

            if self.page.locator("div[role='dialog'][aria-modal='true']").count() > 0:
                self.page.keyboard.press("Escape")
                time.sleep(0.8)
        except Exception:
            pass

    def _limpar_busca_whatsapp(self) -> None:
        """Limpa o campo de pesquisa lateral para que a lista de conversas não fique filtrada."""
        try:
            btn_limpar = self.page.locator(
                "button[aria-label*='Cancelar pesquisa'], button[aria-label*='Limpar'], "
                "span[data-icon='x-alt'], span[data-icon='search-close']"
            )
            if btn_limpar.count() > 0 and btn_limpar.first.is_visible():
                btn_limpar.first.click(timeout=2000)
                time.sleep(0.4)
                return

            campo_busca = self.page.locator(
                "div[contenteditable='true'][data-tab='3'], div[aria-label='Pesquisar texto da caixa']"
            )
            if campo_busca.count() > 0:
                campo_busca.first.click(timeout=2000)
                self.page.keyboard.press("ControlOrMeta+a")
                self.page.keyboard.press("Backspace")
                time.sleep(0.3)
        except Exception:
            pass

    def _chat_corresponde_destino(self, chat_atual: str, destino: str) -> bool:
        """Confirma se o chat aberto corresponde ao contato/número de destino."""
        if not chat_atual or not destino:
            return False
        atual_low = chat_atual.strip().lower()
        dest_low = destino.strip().lower()
        if dest_low in atual_low or atual_low in dest_low:
            return True
        digitos_atual = re.sub(r"\D", "", chat_atual)
        digitos_dest = re.sub(r"\D", "", destino)
        if len(digitos_atual) >= 8 and len(digitos_dest) >= 8:
            return digitos_atual[-8:] == digitos_dest[-8:]
        return False

    def _clicar_chat_visivel_na_lista(self, termo: str) -> bool:
        """Procura o chat em toda a lista lateral (#pane-side) e clica diretamente nele."""
        termo_limpo = termo.strip()
        if not termo_limpo:
            return False

        try:
            # 1. Correspondência exata em toda a lista (sem limite de 30 itens)
            exato = self.page.locator("#pane-side span[title]").filter(
                has_text=re.compile(rf"^{re.escape(termo_limpo)}$", re.IGNORECASE)
            )
            if exato.count() > 0:
                alvo = exato.first
                alvo.scroll_into_view_if_needed()
                alvo.click(timeout=5000)
                time.sleep(1.2)
                return True

            # 2. Correspondência parcial (mesmo mecanismo do Multi-Lojas)
            parcial = self.page.locator("#pane-side span[title]").filter(has_text=termo_limpo)
            if parcial.count() > 0:
                alvo = parcial.first
                alvo.scroll_into_view_if_needed()
                alvo.click(timeout=5000)
                time.sleep(1.2)
                return True

            # 3. Se for telefone, procura pelos últimos 8 dígitos em toda a lista
            digitos_termo = re.sub(r"\D", "", termo_limpo)
            if len(digitos_termo) >= 8:
                titulos = self.page.locator("#pane-side span[title]")
                for i in range(titulos.count()):
                    el = titulos.nth(i)
                    nome = (el.get_attribute("title") or el.inner_text() or "").strip()
                    digitos_nome = re.sub(r"\D", "", nome)
                    if len(digitos_nome) >= 8 and digitos_nome[-8:] == digitos_termo[-8:]:
                        el.scroll_into_view_if_needed()
                        el.click(timeout=5000)
                        time.sleep(1.2)
                        return True
        except Exception as e:
            print(f"[WhatsApp] Aviso ao clicar no chat '{termo_limpo}' na lista lateral: {e}")
        return False

    def _abrir_chat_por_nome_ou_numero(self, termo: str) -> bool:
        """Abre um chat clicando direto na lista lateral ou buscando e clicando no resultado."""
        if not termo or not termo.strip():
            return False

        termo_limpo = termo.strip()
        if self._clicar_chat_visivel_na_lista(termo_limpo):
            return True

        campo_busca = self.page.locator("div[contenteditable='true'][data-tab='3'], div[aria-label='Pesquisar texto da caixa']")
        if campo_busca.count() > 0:
            try:
                campo_busca.first.click(timeout=5000)
                self.page.keyboard.press("ControlOrMeta+a")
                self.page.keyboard.press("Backspace")
                campo_busca.first.press_sequentially(termo_limpo, delay=30)
                time.sleep(1.5)
                if self._clicar_chat_visivel_na_lista(termo_limpo):
                    return True
                self.page.keyboard.press("Enter")
                time.sleep(1.2)
                return self._chat_corresponde_destino(self._obter_chat_aberto_atual() or "", termo_limpo)
            except Exception as e:
                print(f"[WhatsApp] Aviso ao buscar chat '{termo_limpo}': {e}")
            finally:
                self._limpar_busca_whatsapp()
        return False

    def selecionar_chat_alvo(self, nome_chat: Optional[str] = None) -> str:
        """
        Seleciona o chat desejado pelo nome ou abre automaticamente
        a conversa mais recente (não ignorada) caso nenhuma esteja aberta.
        """
        self._fechar_modais_whatsapp()
        chat_a_buscar = (nome_chat if nome_chat is not None else self.chat_alvo or "").strip()

        if chat_a_buscar:
            chat_aberto = self._obter_chat_aberto_atual() or ""
            if self._chat_corresponde_destino(chat_aberto, chat_a_buscar):
                return chat_aberto

            print(f"[WhatsApp] Buscando pelo chat específico: '{chat_a_buscar}'...")
            self._abrir_chat_por_nome_ou_numero(chat_a_buscar)
        else:
            # Se não há chat fixo, verifica se há conversa aberta no painel principal (#main)
            painel_principal = self.page.locator("#main")
            if painel_principal.count() == 0:
                print("[WhatsApp] Nenhuma conversa aberta na tela. Abrindo o primeiro chat válido da lista lateral...")
                chats_laterais = self.page.locator(
                    "#pane-side div[role='row'], #pane-side div[role='listitem'], "
                    "#pane-side div[data-testid='cell-frame-container']"
                )
                clicou = False
                for idx in range(min(chats_laterais.count(), 15)):
                    try:
                        linha = chats_laterais.nth(idx)
                        titulo_el = linha.locator("span[title]").first
                        nome_linha = titulo_el.get_attribute("title") or titulo_el.inner_text().strip() if titulo_el.count() > 0 else ""
                        if nome_linha and self._chat_esta_ignorado(nome_linha):
                            continue
                        linha.click(timeout=5000)
                        time.sleep(1.5)
                        clicou = True
                        break
                    except Exception:
                        continue

                if not clicou:
                    try:
                        titulos = self.page.locator("#pane-side span[title]")
                        for idx in range(min(titulos.count(), 15)):
                            nome_t = titulos.nth(idx).get_attribute("title") or titulos.nth(idx).inner_text().strip()
                            if nome_t and self._chat_esta_ignorado(nome_t):
                                continue
                            titulos.nth(idx).click(timeout=5000)
                            time.sleep(1.5)
                            break
                    except Exception as e:
                        print(f"[WhatsApp] Aviso ao abrir primeiro chat da lista: {e}")

        header_title = self.page.locator(
            "#main header span[title], #main header div[role='button'] span[dir='auto'], #main header span[dir='auto'], header span[title]"
        )
        nome_contato = "Chat_Ativo"
        if header_title.count() > 0:
            texto = header_title.first.inner_text().strip()
            if texto:
                nome_contato = texto

        print(f"[WhatsApp] Conversa ativa: {nome_contato}")
        return nome_contato

    def _carregar_dados_checkpoint(self) -> Optional[Dict[str, Any]]:
        if CHECKPOINT_FILE.exists():
            try:
                with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def _salvar_dados_checkpoint(self, dados: Dict[str, Any]) -> None:
        try:
            with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
                json.dump(dados, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WhatsApp] Aviso ao salvar checkpoint: {e}")

    def _gerar_id_mensagem(self, elemento) -> str:
        try:
            data_id = elemento.evaluate(
                "el => el.getAttribute('data-id') || (el.closest('[data-id]') ? el.closest('[data-id]').getAttribute('data-id') : '')"
            )
            if data_id:
                return data_id
        except Exception:
            data_id = elemento.get_attribute("data-id")
            if data_id:
                return data_id

        pre_text = elemento.get_attribute("data-pre-plain-text") or ""
        texto = elemento.inner_text().strip()
        return f"{pre_text}_{texto}"

    def _extrair_data_hora_mensagem(self, pre_text: str) -> Optional[datetime]:
        """Extrai o timestamp (precisão de minuto) do atributo data-pre-plain-text do WhatsApp."""
        if not pre_text:
            return None
        # Padrão PT-BR: [08:35, 26/11/2026]
        m_pt = re.search(r"\[(\d{1,2}:\d{2}),\s*(\d{2}/\d{2}/\d{4})\]", pre_text)
        if m_pt:
            try:
                return datetime.strptime(f"{m_pt.group(2)} {m_pt.group(1)}", "%d/%m/%Y %H:%M")
            except ValueError:
                pass
        # Padrão EN fallback: [8:35 AM, 11/26/2026]
        m_en = re.search(r"\[(\d{1,2}:\d{2}\s*[AP]M),\s*(\d{1,2}/\d{1,2}/\d{4})\]", pre_text, re.IGNORECASE)
        if m_en:
            try:
                return datetime.strptime(f"{m_en.group(2)} {m_en.group(1).upper()}", "%m/%d/%Y %I:%M %p")
            except ValueError:
                pass
        return None

    def _eh_mensagem_alerta_bot(self, texto: str) -> bool:
        """Identifica mensagens de alerta enviadas pelo próprio robô para evitar loop."""
        texto_low = (texto or "").lower()
        assinaturas_alerta = (
            "alerta nota inexistente",
            "alerta lançador de notas",
            "alerta boleto divergente",
            "alerta de boleto divergente",
            "alerta de erro no lançamento",
        )
        return any(assinatura in texto_low for assinatura in assinaturas_alerta)

    def _extrair_notas_do_chat_aberto(self, nome_contato: str, checkpoint: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        """Lê os balões da conversa atualmente aberta no #main e enfileira novas notas."""
        if self._chat_esta_ignorado(nome_contato):
            print(f"[WhatsApp] 🚫 Chat '{nome_contato}' está na lista de ignorados. Pulando...")
            return [], []

        time.sleep(1)
        # Usa preferencialmente div[data-pre-plain-text] para evitar ler o mesmo balão 2 vezes
        locators_mensagens = self.page.locator("#main div[data-pre-plain-text]")
        if locators_mensagens.count() == 0:
            locators_mensagens = self.page.locator("#main div.message-in, #main div.message-out")
        total_elementos = locators_mensagens.count()

        if total_elementos == 0:
            print(f"[WhatsApp] Nenhuma mensagem encontrada no painel da conversa '{nome_contato}'.")
            return [], []

        print(f"[WhatsApp] Lendo mensagens de '{nome_contato}' ({total_elementos} balões detectados)...")
        ids_processados_set = set(checkpoint.get("ids_processados", []))
        marco_zero_minuto: Optional[datetime] = None
        if checkpoint.get("criado_em"):
            try:
                marco_zero_minuto = datetime.fromtimestamp(float(checkpoint["criado_em"])).replace(second=0, microsecond=0)
            except Exception:
                marco_zero_minuto = None

        novas_notas: List[str] = []
        novos_ids: List[str] = []

        # Lê as últimas 30 mensagens para performance e precisão
        inicio = max(0, total_elementos - 30)
        for i in range(inicio, total_elementos):
            try:
                elemento = locators_mensagens.nth(i)
                pre_text = elemento.get_attribute("data-pre-plain-text") or ""

                # Ignora qualquer mensagem com horário estritamente anterior ao marco zero (mesmo se rolar o histórico)
                if marco_zero_minuto and pre_text:
                    msg_dt = self._extrair_data_hora_mensagem(pre_text)
                    if msg_dt and msg_dt < marco_zero_minuto:
                        continue

                msg_id = self._gerar_id_mensagem(elemento)

                if msg_id in ids_processados_set or msg_id in novos_ids:
                    continue

                # Tenta capturar apenas o texto puro da mensagem (evitando horário/status)
                texto_elem = elemento.locator("span.selectable-text, span.copyable-text, span[dir='ltr']")
                if texto_elem.count() > 0:
                    texto = texto_elem.first.inner_text().strip()
                else:
                    texto = elemento.inner_text().strip()

                if not texto:
                    continue

                novos_ids.append(msg_id)

                if self._eh_mensagem_alerta_bot(texto):
                    continue

                # Identifica se há remetente específico gravado no atributo data-pre-plain-text
                origem_especifica = nome_contato
                if pre_text:
                    match_remetente = re.search(r"\]\s*([^:]+):", pre_text)
                    if match_remetente:
                        nome_remetente = match_remetente.group(1).strip()
                        if nome_remetente and nome_remetente.lower() != "você":
                            origem_especifica = nome_remetente

                numeros = extrair_numeros_notas(texto)
                if numeros:
                    print(f"  [WhatsApp] Mensagem identificada de '{origem_especifica}': '{texto}' -> Notas: {numeros}")
                    for num in numeros:
                        foi_inserida = inserir_nota(numero_nota=num, origem_contato=origem_especifica)
                        if foi_inserida:
                            print(f"  -> [NOVA NOTA CAPTURADA] Nº {num} ({origem_especifica}) enfileirada no banco local.")
                            novas_notas.append(num)
                        else:
                            status_atual = obter_status_nota(origem_especifica, num)
                            if status_atual in ("lancado", "duplicada_erp"):
                                msg_dup = "Nota já lançada anteriormente (reenviada no WhatsApp)"
                                registrar_duplicidade(num, origem_especifica, msg_dup)
                                print(f"  -> [DUPLICIDADE] Nº {num} ({origem_especifica}) já lançada antes. Registrada em duplicidades.csv.")
                            else:
                                print(f"  -> [INFO] Nº {num} ({origem_especifica}) já estava no banco de dados (status: {status_atual}).")

            except Exception as err:
                continue

        return novas_notas, novos_ids

    def _obter_chat_aberto_atual(self) -> Optional[str]:
        """Retorna o nome do chat atualmente aberto no painel #main, ou None se não houver nenhum."""
        try:
            if self.page.locator("#main").count() == 0:
                return None
            header_title = self.page.locator(
                "#main header span[title], #main header div[role='button'] span[dir='auto'], #main header span[dir='auto']"
            )
            if header_title.count() > 0:
                texto = header_title.first.inner_text().strip()
                if texto:
                    return texto
            return "Chat_Ativo"
        except Exception:
            return None

    def ler_mensagens_e_enfileirar(self) -> Tuple[int, List[str]]:
        """
        Varre TODAS as conversas com mensagens novas/não lidas (Multi-Lojas).
        Se várias lojas enviarem mensagens simultaneamente, abre cada uma
        e enfileira todas as notas no SQLite.
        """
        if not self.page or self.page.is_closed():
            print("[WhatsApp - Auto-Recuperação] 🔄 Janela do WhatsApp fechada detectada! Reabrindo...")
            self.iniciar()
            if not self.abrir_e_garantir_login():
                return 0, []

        self._fechar_modais_whatsapp()
        checkpoint = self._carregar_dados_checkpoint()
        alvo_fixo = (self.chat_alvo or "").strip()

        # 1. Se for a primeira execução (ou após zerar checkpoint), define o marco zero em 100% das mensagens atuais
        if not checkpoint:
            nome_contato = self.selecionar_chat_alvo(alvo_fixo if alvo_fixo else None)
            locators = self.page.locator("#main div[data-pre-plain-text]")
            if locators.count() == 0:
                locators = self.page.locator("#main div.message-in, #main div.message-out")
            total_locs = locators.count()
            ids_iniciais = []
            for i in range(total_locs):
                ids_iniciais.append(self._gerar_id_mensagem(locators.nth(i)))

            # Salva TODAS as mensagens visíveis atualmente para não ler notas antigas após zerar checkpoint
            ids_historico = ids_iniciais
            ultimo_id = ids_iniciais[-1] if ids_iniciais else ""

            checkpoint = {
                "criado_em": time.time(),
                "ultimo_id_processado": ultimo_id,
                "ids_processados": ids_historico[-500:],
            }
            self._salvar_dados_checkpoint(checkpoint)
            print("[WhatsApp] 🚀 Marco zero definido! Mensagens anteriores ignoradas; monitorando apenas novas mensagens.")

        todas_novas_notas: List[str] = []
        todos_novos_ids: List[str] = []

        # 2. Se houver um Grupo Exclusivo (chat alvo fixo), foca exclusivamente nele
        if alvo_fixo:
            nome_contato = self.selecionar_chat_alvo(alvo_fixo)
            novas, ids = self._extrair_notas_do_chat_aberto(nome_contato, checkpoint)
            todas_novas_notas.extend(novas)
            todos_novos_ids.extend(ids)
        else:
            # 3. VARREDURA MULTI-LOJAS: lê SEMPRE o chat já aberto na tela + todas as conversas não lidas válidas
            chats_lidos_no_ciclo = set()

            # 3A. Se já houver uma conversa aberta na tela (#main), lê ela imediatamente
            chat_aberto = self._obter_chat_aberto_atual()
            if chat_aberto:
                if self._chat_esta_ignorado(chat_aberto):
                    print(f"[WhatsApp] 🚫 Chat aberto '{chat_aberto}' está na lista de ignorados. Pulando...")
                else:
                    novas, ids = self._extrair_notas_do_chat_aberto(chat_aberto, checkpoint)
                    todas_novas_notas.extend(novas)
                    todos_novos_ids.extend(ids)
                    chats_lidos_no_ciclo.add(chat_aberto.lower())

            # 3B. Detecta outras conversas com selo de mensagem não lida
            seletor_badges_nao_lidos = (
                "#pane-side span[aria-label*='não lida'], "
                "#pane-side span[aria-label*='não lidas'], "
                "#pane-side span[aria-label*='unread'], "
                "#pane-side span[aria-label*='Unread'], "
                "#pane-side span[data-testid='icon-unread-count'], "
                "#pane-side span[data-icon='unread-count'], "
                "#pane-side [data-testid='unread-count']"
            )

            badges_nao_lidos = self.page.locator(seletor_badges_nao_lidos)
            total_badges = badges_nao_lidos.count()

            if total_badges > 0:
                # Lê os títulos dos chats não lidos na barra lateral antes de clicar (para não abrir chats ignorados)
                nomes_nao_lidos: List[str] = []
                try:
                    nomes_nao_lidos = self.page.evaluate("""() => {
                        const badges = document.querySelectorAll(
                            "#pane-side span[aria-label*='não lida'], #pane-side span[aria-label*='não lidas'], " +
                            "#pane-side span[aria-label*='unread'], #pane-side span[aria-label*='Unread'], " +
                            "#pane-side span[data-testid='icon-unread-count'], #pane-side span[data-icon='unread-count'], " +
                            "#pane-side [data-testid='unread-count']"
                        );
                        const nomes = [];
                        for (const b of badges) {
                            const row = b.closest("div[role='row'], div[role='listitem'], div[data-testid='cell-frame-container'], div[tabindex='-1']");
                            if (row) {
                                const titleEl = row.querySelector("span[title]");
                                const nome = titleEl ? (titleEl.getAttribute("title") || titleEl.textContent || "").trim() : "";
                                if (nome && !nomes.includes(nome)) {
                                    nomes.push(nome);
                                }
                            }
                        }
                        return nomes;
                    }""")
                except Exception:
                    nomes_nao_lidos = []

                if nomes_nao_lidos:
                    for nome_loja in nomes_nao_lidos[:15]:
                        try:
                            if self._chat_esta_ignorado(nome_loja):
                                print(f"[WhatsApp] 🚫 Grupo/Chat '{nome_loja}' ignorado conforme filtro (sem abrir).")
                                continue

                            if nome_loja.lower() in chats_lidos_no_ciclo:
                                continue

                            chat_row = self.page.locator("#pane-side span[title]").filter(has_text=nome_loja).first
                            if chat_row.count() == 0:
                                continue

                            chat_row.click()
                            time.sleep(1.2)

                            print(f"[WhatsApp Multi-Lojas] Abrindo conversa de: {nome_loja}...")
                            novas, ids = self._extrair_notas_do_chat_aberto(nome_loja, checkpoint)
                            todas_novas_notas.extend(novas)
                            todos_novos_ids.extend(ids)
                            chats_lidos_no_ciclo.add(nome_loja.lower())
                        except Exception as err_chat:
                            print(f"[WhatsApp] Aviso ao processar conversa '{nome_loja}': {err_chat}")
                            continue
                else:
                    # Fallback caso o DOM da barra lateral não exponha span[title]
                    chats_a_processar = min(total_badges, 15)
                    for _ in range(chats_a_processar):
                        try:
                            badges_atual = self.page.locator(seletor_badges_nao_lidos)
                            if badges_atual.count() == 0:
                                break

                            badge_alvo = badges_atual.first
                            badge_alvo.click()
                            time.sleep(1.2)

                            nome_loja = self._obter_chat_aberto_atual() or "Loja_WhatsApp"

                            if self._chat_esta_ignorado(nome_loja):
                                print(f"[WhatsApp] 🚫 Chat '{nome_loja}' está na lista de ignorados. Pulando...")
                                continue

                            if nome_loja.lower() in chats_lidos_no_ciclo:
                                continue

                            print(f"[WhatsApp Multi-Lojas] Abrindo conversa de: {nome_loja}...")
                            novas, ids = self._extrair_notas_do_chat_aberto(nome_loja, checkpoint)
                            todas_novas_notas.extend(novas)
                            todos_novos_ids.extend(ids)
                            chats_lidos_no_ciclo.add(nome_loja.lower())

                        except Exception as err_chat:
                            print(f"[WhatsApp] Aviso ao processar conversa não lida: {err_chat}")
                            continue

            # 3C. Se nenhum chat válido estava aberto e nenhum badge válido foi lido, abre o primeiro chat válido
            if not chats_lidos_no_ciclo and not chat_aberto:
                nome_contato = self.selecionar_chat_alvo()
                novas, ids = self._extrair_notas_do_chat_aberto(nome_contato, checkpoint)
                todas_novas_notas.extend(novas)
                todos_novos_ids.extend(ids)

        # 4. Atualiza o checkpoint no arquivo JSON
        if todos_novos_ids:
            lista_atual = checkpoint.get("ids_processados", [])
            lista_atual.extend(todos_novos_ids)
            checkpoint["ids_processados"] = lista_atual[-500:]
            checkpoint["ultimo_id_processado"] = todos_novos_ids[-1]
            checkpoint["atualizado_em"] = time.time()
            self._salvar_dados_checkpoint(checkpoint)
            print(f"[WhatsApp] Varredura concluída: {len(todas_novas_notas)} nova(s) nota(s) enfileirada(s).")
        else:
            print("[WhatsApp] Nenhuma nota nova recebida desde a última verificação.")

        return len(todas_novas_notas), todas_novas_notas

    def reagir_mensagem_nota_lancada(self, numero_nota: str, origem_contato: Optional[str] = None) -> bool:
        """
        Localiza a mensagem correspondente à nota lançada no WhatsApp Web
        e reage com o emoji ✅ para confirmar à loja o lançamento bem-sucedido.
        Se origem_contato for informada, abre a conversa da loja antes de reagir.
        """
        try:
            alvo_fixo = (self.chat_alvo or "").strip()
            destino_reacao = alvo_fixo if alvo_fixo else origem_contato
            if destino_reacao and destino_reacao not in ("Chat_Ativo", "Loja_WhatsApp"):
                # Só pesquisa se o chat atual aberto ainda não for o do grupo/origem
                chat_atual = self._obter_chat_aberto_atual() or ""
                if not self._chat_corresponde_destino(chat_atual, destino_reacao):
                    self.selecionar_chat_alvo(destino_reacao)
                    time.sleep(1)

            print(f"[WhatsApp] Procurando mensagem da NF {numero_nota} para reagir com 👍...")
            bolhas = self.page.locator(
                f"#main div[data-id]:has-text('{numero_nota}'), "
                f"#main div.message-in:has-text('{numero_nota}'), "
                f"#main div.message-out:has-text('{numero_nota}')"
            )
            if bolhas.count() == 0:
                print(f"[WhatsApp] Aviso: mensagem da NF {numero_nota} não encontrada no chat aberto para reagir.")
                return False

            alvo = bolhas.last
            alvo.scroll_into_view_if_needed()
            time.sleep(0.3)

            # Move o ponteiro para o canto antes para limpar tooltips residuais de reações anteriores
            try:
                self.page.mouse.move(10, 10)
            except Exception:
                pass

            # Tenta hover no balão interno (com fallback force=True se algum overlay interceptar)
            bolha_interna = alvo.locator("div[data-pre-plain-text], span.selectable-text").first
            alvo_hover = bolha_interna if bolha_interna.count() > 0 else alvo
            try:
                alvo_hover.hover(timeout=5000)
            except Exception:
                try:
                    alvo_hover.hover(force=True, timeout=3000)
                except Exception:
                    pass
            time.sleep(0.6)

            btn_react = alvo.locator("[aria-label='Reagir']")
            if btn_react.count() == 0:
                btn_react = alvo.locator(
                    "button[aria-label*='Reagir'], div[aria-label*='Reagir'], "
                    "button[aria-label*='React'], div[aria-label*='React'], "
                    "[data-testid='reaction-button'], span[data-icon='react'], span[data-icon='smiley']"
                )
            abriu_menu = False

            if btn_react.count() > 0 and btn_react.first.is_visible():
                btn_react.first.click(timeout=5000)
                time.sleep(0.7)
                abriu_menu = True
            else:
                # Plano B: clique com botão direito no balão -> "Reagir"
                try:
                    alvo_hover.click(button="right", force=True, timeout=5000)
                    time.sleep(0.5)
                    item_reagir = self.page.locator(
                        "[role='menuitem']:has-text('Reagir'), div[aria-label='Reagir'], li:has-text('Reagir')"
                    )
                    if item_reagir.count() > 0 and item_reagir.first.is_visible():
                        item_reagir.first.click(timeout=5000)
                        time.sleep(0.7)
                        abriu_menu = True
                    else:
                        self.page.keyboard.press("Escape")
                except Exception:
                    pass

            if not abriu_menu:
                print(f"[WhatsApp] Aviso: botão 'Reagir' não apareceu para a NF {numero_nota}.")
                return False

            # Procura e clica no 👍 exclusivamente FORA de #main e #pane-side (no popup flutuante)
            coords_joinha = self.page.evaluate("""() => {
                const candidatos = document.querySelectorAll(
                    "[aria-label*='👍'], [aria-label*='polegar' i], [aria-label*='thumbs' i], " +
                    "[data-emoji='👍'], img[alt='👍'], span, button, div[role='button']"
                );
                for (const el of candidatos) {
                    if (el.closest('#main') || el.closest('#pane-side')) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0 || rect.width > 120 || rect.height > 120) continue;
                    const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                    const alt = (el.getAttribute('alt') || '');
                    const emoji = (el.getAttribute('data-emoji') || '');
                    const txt = (el.textContent || '').trim();
                    if (
                        emoji === '👍' || alt === '👍' || txt === '👍' ||
                        aria.includes('👍') || aria.includes('polegar') || aria.includes('thumbs')
                    ) {
                        return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
                    }
                }
                return null;
            }""")

            if coords_joinha:
                self.page.mouse.click(coords_joinha["x"], coords_joinha["y"])
                time.sleep(0.6)
                try:
                    self.page.mouse.move(10, 10)
                except Exception:
                    pass

                # Confirma dentro do próprio balão se a reação 👍 apareceu de fato
                reacao_no_balao = alvo.locator(
                    "[aria-label*='reação'], [aria-label*='👍'], img[alt='👍'], [data-emoji='👍']"
                )
                if reacao_no_balao.count() > 0:
                    # Mantém o chat rolado para o final (mensagens mais recentes) após reagir
                    try:
                        self.page.evaluate("""() => {
                            const app = document.querySelector('#main [role="application"]');
                            const scrollArea = app ? app.parentElement : document.querySelector('#main div.copyable-area');
                            if (scrollArea) scrollArea.scrollTop = scrollArea.scrollHeight;
                        }""")
                    except Exception:
                        pass
                    print(f"[WhatsApp] 👍 Reação adicionada na mensagem da NF {numero_nota}!")
                    return True

                print(f"[WhatsApp] Aviso: clique em ({int(coords_joinha['x'])}, {int(coords_joinha['y'])}) realizado, mas a reação 👍 não apareceu no balão da NF {numero_nota}.")
                return False

            # Diagnóstico: lista apenas elementos visíveis fora de #main e #pane-side
            labels_debug = []
            try:
                labels_debug = self.page.evaluate("""() => {
                    const els = document.querySelectorAll("button, [role='button'], [aria-label], img[alt]");
                    const res = [];
                    for (const el of els) {
                        if (el.closest('#main') || el.closest('#pane-side')) continue;
                        const rect = el.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        const info = el.getAttribute('aria-label') || el.getAttribute('alt') || (el.textContent || '').trim();
                        if (info) res.push(info.slice(0, 40));
                    }
                    return res.slice(-15);
                }""")
            except Exception:
                pass
            print(f"[WhatsApp] Aviso: menu de reação abriu, mas o emoji 👍 não foi localizado fora do #main. Opções vistas: {labels_debug}")
            self.page.keyboard.press("Escape")
            return False
        except Exception as e:
            print(f"[WhatsApp] Aviso ao reagir à mensagem da NF {numero_nota}: {e}")
            return False

    def enviar_alerta(self, numero_destinatario: str, mensagem_alerta: str) -> bool:
        """
        Envia uma mensagem de alerta para um número de WhatsApp específico configurado na interface.
        Exemplo de número: 5511999998888 ou o nome exato do contato.
        """
        if not numero_destinatario or not numero_destinatario.strip():
            return False

        destino = numero_destinatario.strip()
        print(f"[WhatsApp - Alerta] Enviando notificação para '{destino}'...")

        try:
            chat_atual = self._obter_chat_aberto_atual() or ""
            if not self._chat_corresponde_destino(chat_atual, destino):
                self._abrir_chat_por_nome_ou_numero(destino)
                chat_atual = self._obter_chat_aberto_atual() or ""

            # Trava de segurança: nunca envia alerta no chat errado
            if not self._chat_corresponde_destino(chat_atual, destino):
                print(
                    f"[WhatsApp - Alerta] ⚠️ Não foi possível confirmar o chat '{destino}' "
                    f"(chat aberto: '{chat_atual}'). Alerta abortado para evitar envio na conversa errada."
                )
                return False

            # 2. Caixa de digitação da mensagem
            campo_msg = self.page.locator("div[contenteditable='true'][data-tab='10'], footer div[contenteditable='true']")
            if campo_msg.count() > 0:
                campo_msg.first.click(timeout=5000)
                campo_msg.first.fill(mensagem_alerta)
                self.page.keyboard.press("Enter")
                time.sleep(1)
                print(f"[WhatsApp - Alerta] Notificação enviada com sucesso para '{destino}'!")
                return True

            return False
        except Exception as e:
            print(f"[WhatsApp - Alerta] Erro ao enviar alerta: {e}")
            self._limpar_busca_whatsapp()
            return False

    def fechar(self) -> None:
        try:
            if self.context:
                self.context.close()
            if self._playwright and not self._external_playwright:
                self._playwright.stop()
        except Exception:
            pass
