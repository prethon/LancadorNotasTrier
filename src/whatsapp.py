import json
import time
import re
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
from .database import inserir_nota

CHECKPOINT_FILE = DATA_DIR / "whatsapp_checkpoint.json"


class WhatsAppMonitor:
    def __init__(
        self,
        headless: Optional[bool] = None,
        playwright_instance=None,
        chats_ignorar: Optional[str] = None,
    ):
        if headless is None:
            self.headless = not MODO_TESTE
        else:
            self.headless = headless

        self._external_playwright = playwright_instance is not None
        self._playwright = playwright_instance
        self.chats_ignorar = WHATSAPP_IGNORAR if chats_ignorar is None else chats_ignorar
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

    def selecionar_chat_alvo(self, nome_chat: Optional[str] = None) -> str:
        """
        Seleciona o chat desejado pelo nome ou abre automaticamente
        a conversa mais recente (não ignorada) caso nenhuma esteja aberta.
        """
        self._fechar_modais_whatsapp()
        chat_a_buscar = nome_chat or WHATSAPP_CHAT_ALVO

        if chat_a_buscar:
            print(f"[WhatsApp] Buscando pelo chat específico: '{chat_a_buscar}'...")
            campo_busca = self.page.locator("div[contenteditable='true'][data-tab='3'], div[aria-label='Pesquisar texto da caixa']")
            if campo_busca.count() > 0:
                try:
                    campo_busca.first.click(timeout=5000)
                    campo_busca.first.fill(chat_a_buscar)
                    self.page.keyboard.press("Enter")
                    time.sleep(1.5)
                except Exception as e:
                    print(f"[WhatsApp] Aviso ao buscar chat '{chat_a_buscar}': {e}")
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
        data_id = elemento.get_attribute("data-id")
        if data_id:
            return data_id

        pre_text = elemento.get_attribute("data-pre-plain-text") or ""
        texto = elemento.inner_text().strip()
        return f"{pre_text}_{texto}"

    def _eh_mensagem_alerta_bot(self, texto: str) -> bool:
        """Identifica mensagens de alerta enviadas pelo próprio robô para evitar loop."""
        texto_low = (texto or "").lower()
        assinaturas_alerta = (
            "alerta nota inexistente",
            "alerta lançador de notas",
            "alerta boleto divergente",
            "alerta de boleto divergente",
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
        novas_notas: List[str] = []
        novos_ids: List[str] = []

        # Lê as últimas 30 mensagens para performance e precisão
        inicio = max(0, total_elementos - 30)
        for i in range(inicio, total_elementos):
            try:
                elemento = locators_mensagens.nth(i)
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
                pre_text = elemento.get_attribute("data-pre-plain-text") or ""
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
                            print(f"  -> [INFO] Nº {num} ({origem_especifica}) já estava no banco de dados.")

            except Exception as err:
                continue

        return novas_notas, novos_ids

    def ler_mensagens_e_enfileirar(self) -> Tuple[int, List[str]]:
        """
        Varre TODAS as conversas com mensagens novas/não lidas (Multi-Lojas).
        Se várias lojas enviarem mensagens simultaneamente, abre cada uma
        e enfileira todas as notas no SQLite.
        """
        self._fechar_modais_whatsapp()
        checkpoint = self._carregar_dados_checkpoint()

        # 1. Se for a primeira execução (ou após zerar checkpoint), define o marco zero em 100% das mensagens atuais
        if not checkpoint:
            nome_contato = self.selecionar_chat_alvo()
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

        # 2. Se houver um chat alvo fixo no .env, foca nele
        if WHATSAPP_CHAT_ALVO:
            nome_contato = self.selecionar_chat_alvo(WHATSAPP_CHAT_ALVO)
            novas, ids = self._extrair_notas_do_chat_aberto(nome_contato, checkpoint)
            todas_novas_notas.extend(novas)
            todos_novos_ids.extend(ids)
        else:
            # 3. VARREDURA MULTI-LOJAS: Detecta conversas com mensagens não lidas diretamente pelo selo/badge
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
                print(f"[WhatsApp Multi-Lojas] Encontrada(s) {total_badges} conversa(s) com novas mensagens!")

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

                            chat_row = self.page.locator("#pane-side span[title]").filter(has_text=nome_loja).first
                            if chat_row.count() == 0:
                                continue

                            chat_row.click()
                            time.sleep(1.2)

                            print(f"[WhatsApp Multi-Lojas] Abrindo conversa de: {nome_loja}...")
                            novas, ids = self._extrair_notas_do_chat_aberto(nome_loja, checkpoint)
                            todas_novas_notas.extend(novas)
                            todos_novos_ids.extend(ids)
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

                            header_span = self.page.locator(
                                "#main header span[title], #main header div[role='button'] span[dir='auto'], #main header span[dir='auto']"
                            )
                            nome_loja = "Loja_WhatsApp"
                            if header_span.count() > 0:
                                txt = header_span.first.inner_text().strip()
                                if txt:
                                    nome_loja = txt

                            if self._chat_esta_ignorado(nome_loja):
                                print(f"[WhatsApp] 🚫 Chat '{nome_loja}' está na lista de ignorados. Pulando...")
                                continue

                            print(f"[WhatsApp Multi-Lojas] Abrindo conversa de: {nome_loja}...")
                            novas, ids = self._extrair_notas_do_chat_aberto(nome_loja, checkpoint)
                            todas_novas_notas.extend(novas)
                            todos_novos_ids.extend(ids)

                        except Exception as err_chat:
                            print(f"[WhatsApp] Aviso ao processar conversa não lida: {err_chat}")
                            continue
            else:
                # Se não havia nenhuma conversa com bolinha verde, lê a conversa que já está aberta ou a do topo
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
            if origem_contato and origem_contato not in ("Chat_Ativo", "Loja_WhatsApp"):
                # Só pesquisa se o chat atual aberto ainda não for o da origem
                header_title = self.page.locator(
                    "#main header span[title], #main header div[role='button'] span[dir='auto'], #main header span[dir='auto']"
                )
                chat_atual = header_title.first.inner_text().strip() if header_title.count() > 0 else ""
                if origem_contato.lower() not in chat_atual.lower():
                    self.selecionar_chat_alvo(origem_contato)
                    time.sleep(1)

            print(f"[WhatsApp] Procurando mensagem da NF {numero_nota} para reagir com ✅...")
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

            # Tenta hover no balão interno (e depois no container) para exibir o botão Reagir
            bolha_interna = alvo.locator("div[data-pre-plain-text], span.selectable-text").first
            if bolha_interna.count() > 0:
                bolha_interna.hover()
            else:
                alvo.hover()
            time.sleep(0.6)

            seletores_react = (
                "button[aria-label*='Reagir'], div[aria-label*='Reagir'], "
                "button[aria-label*='React'], div[aria-label*='React'], "
                "[data-testid='reaction-button'], span[data-icon='react'], span[data-icon='smiley']"
            )
            btn_react = alvo.locator(seletores_react)
            abriu_menu = False

            if btn_react.count() > 0 and btn_react.first.is_visible():
                btn_react.first.click(timeout=5000)
                time.sleep(0.6)
                abriu_menu = True
            else:
                # Plano B: clique com botão direito no balão -> "Reagir"
                try:
                    alvo_ctx = bolha_interna if bolha_interna.count() > 0 else alvo
                    alvo_ctx.click(button="right", timeout=5000)
                    time.sleep(0.5)
                    item_reagir = self.page.locator(
                        "[role='menuitem']:has-text('Reagir'), div[aria-label='Reagir'], li:has-text('Reagir')"
                    )
                    if item_reagir.count() > 0 and item_reagir.first.is_visible():
                        item_reagir.first.click(timeout=5000)
                        time.sleep(0.6)
                        abriu_menu = True
                    else:
                        self.page.keyboard.press("Escape")
                except Exception:
                    pass

            if not abriu_menu:
                print(f"[WhatsApp] Aviso: botão 'Reagir' não apareceu para a NF {numero_nota}.")
                return False

            # 1. Tenta clicar direto no ✅ caso já esteja nos emojis rápidos/recentes (.last = popup recém-aberto)
            seletor_check = (
                "[data-emoji='✅'], [aria-label*='✅'], img[alt='✅'], "
                "[aria-label*='Marca de seleção'], [aria-label*='marca de seleção']"
            )
            emoji_check = self.page.locator(seletor_check)
            if emoji_check.count() > 0 and emoji_check.last.is_visible():
                emoji_check.last.click(timeout=5000)
                print(f"[WhatsApp] ✅ Reação adicionada com sucesso na mensagem da NF {numero_nota}!")
                return True

            # 2. Abre o seletor completo '+' ('Mais reações' / 'Mais emojis') para escolher ✅
            btn_mais = self.page.locator(
                "[aria-label*='Mais reaç'], [aria-label*='Mais emoji'], "
                "[aria-label*='More reaction'], [data-icon*='plus']"
            )
            if btn_mais.count() > 0 and btn_mais.last.is_visible():
                btn_mais.last.click(timeout=5000)
                time.sleep(0.6)

                campo_busca_emoji = self.page.locator(
                    "input[placeholder*='Pesquisar'], input[aria-label*='Pesquisar'], "
                    "div[contenteditable='true'][aria-label*='Pesquisar'], input[placeholder*='Search']"
                )
                for termo_busca in ("marca", "check"):
                    if campo_busca_emoji.count() > 0:
                        campo_busca_emoji.last.click(timeout=3000)
                        campo_busca_emoji.last.fill(termo_busca)
                        time.sleep(0.6)

                    emoji_check = self.page.locator(seletor_check)
                    if emoji_check.count() > 0 and emoji_check.last.is_visible():
                        emoji_check.last.click(timeout=5000)
                        print(f"[WhatsApp] ✅ Reação adicionada com sucesso na mensagem da NF {numero_nota}!")
                        return True

            # 3. Fallback para 👍 da barra rápida
            emoji_joinha = self.page.locator(
                "[data-emoji='👍'], [aria-label*='👍'], img[alt='👍'], "
                "[aria-label*='polegar'], [aria-label*='Reagir com']"
            )
            if emoji_joinha.count() > 0 and emoji_joinha.last.is_visible():
                emoji_joinha.last.click(timeout=5000)
                print(f"[WhatsApp] 👍 Reação adicionada na mensagem da NF {numero_nota}!")
                return True

            # Diagnóstico real nos últimos elementos flutuantes do DOM (sem depender de role='dialog')
            labels_debug = []
            try:
                labels_debug = self.page.evaluate("""() => {
                    const els = document.querySelectorAll(
                        "[role='menu'] [aria-label], [role='application'] [aria-label], " +
                        "div[tabindex='-1'] [aria-label], body > div:last-child [aria-label], " +
                        "body > div:last-child img[alt]"
                    );
                    return Array.from(els).slice(-12).map(el => el.getAttribute("aria-label") || el.getAttribute("alt") || "").filter(Boolean);
                }""")
            except Exception:
                pass
            print(f"[WhatsApp] Aviso: menu de reação abriu, mas o emoji ✅ não foi localizado para a NF {numero_nota}. Opções vistas: {labels_debug}")
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
            # 1. Tenta buscar pelo campo de pesquisa
            campo_busca = self.page.locator("div[contenteditable='true'][data-tab='3'], div[aria-label='Pesquisar texto da caixa']")
            if campo_busca.count() > 0:
                campo_busca.first.click()
                campo_busca.first.fill(destino)
                self.page.keyboard.press("Enter")
                time.sleep(1.5)

            # 2. Caixa de digitação da mensagem
            campo_msg = self.page.locator("div[contenteditable='true'][data-tab='10'], footer div[contenteditable='true']")
            if campo_msg.count() > 0:
                campo_msg.first.click()
                campo_msg.first.fill(mensagem_alerta)
                self.page.keyboard.press("Enter")
                time.sleep(1)
                print(f"[WhatsApp - Alerta] Notificação enviada com sucesso para '{destino}'!")
                return True

            return False
        except Exception as e:
            print(f"[WhatsApp - Alerta] Erro ao enviar alerta: {e}")
            return False

    def fechar(self) -> None:
        try:
            if self.context:
                self.context.close()
            if self._playwright and not self._external_playwright:
                self._playwright.stop()
        except Exception:
            pass
