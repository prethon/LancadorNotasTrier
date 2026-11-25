import json
import time
import re
from pathlib import Path
from typing import Tuple, List, Optional, Dict, Any
from playwright.sync_api import sync_playwright, BrowserContext, Page
from .config import (
    WHATSAPP_SESSION_DIR,
    WHATSAPP_CHAT_ALVO,
    MODO_TESTE,
    DATA_DIR,
)
from .extractor import extrair_numeros_notas
from .database import inserir_nota

CHECKPOINT_FILE = DATA_DIR / "whatsapp_checkpoint.json"


class WhatsAppMonitor:
    def __init__(self, headless: bool = False):
        self.headless = headless if not MODO_TESTE else False
        self._playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def __enter__(self):
        self.iniciar()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.fechar()

    def iniciar(self) -> None:
        """Inicializa o navegador com perfil persistente para manter a sessão salva."""
        self._playwright = sync_playwright().start()
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--disable-infobars",
        ]
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(WHATSAPP_SESSION_DIR),
            headless=self.headless,
            viewport={"width": 1280, "height": 800},
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
        print("[WhatsApp] Verificando sessão...")

        while time.time() - tempo_inicial < timeout_login:
            if self.page.locator(seletor_painel_conversas).count() > 0:
                print("[WhatsApp] Sessão autenticada e pronta para uso!")
                return True

            if self.page.locator(seletor_qrcode).count() > 0:
                print("[WhatsApp] Aguardando escaneamento do QR Code no navegador aberto...")
                time.sleep(3)
                continue

            time.sleep(1)

        print("[WhatsApp] Tempo limite excedido para autenticação no WhatsApp Web.")
        return False

    def selecionar_chat_alvo(self, nome_chat: Optional[str] = None) -> str:
        """
        Seleciona o chat desejado pelo nome ou abre automaticamente
        a conversa mais recente caso nenhuma esteja aberta.
        """
        chat_a_buscar = nome_chat or WHATSAPP_CHAT_ALVO

        if chat_a_buscar:
            print(f"[WhatsApp] Buscando pelo chat específico: '{chat_a_buscar}'...")
            campo_busca = self.page.locator("div[contenteditable='true'][data-tab='3'], div[aria-label='Pesquisar texto da caixa']")
            if campo_busca.count() > 0:
                campo_busca.first.click()
                campo_busca.first.fill(chat_a_buscar)
                self.page.keyboard.press("Enter")
                time.sleep(1.5)
        else:
            # Se não há chat fixo, verifica se há conversa aberta no painel principal (#main)
            painel_principal = self.page.locator("#main")
            if painel_principal.count() == 0:
                print("[WhatsApp] Nenhuma conversa aberta na tela. Abrindo o primeiro chat da lista...")
                primeiro_chat = self.page.locator("#pane-side div[role='listitem'], #pane-side div[tabindex='-1']")
                if primeiro_chat.count() > 0:
                    primeiro_chat.first.click()
                    time.sleep(1.5)

        header_title = self.page.locator("#main header span[title], #main header div[role='button'] span[dir='auto'], header span[title]")
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

    def _extrair_notas_do_chat_aberto(self, nome_contato: str, checkpoint: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        """Lê os balões da conversa atualmente aberta no #main e enfileira novas notas."""
        time.sleep(1)
        locators_mensagens = self.page.locator("#main div.message-in, #main div.message-out, div.message-in, div.message-out")
        total_elementos = locators_mensagens.count()

        if total_elementos == 0:
            return [], []

        ids_processados_set = set(checkpoint.get("ids_processados", []))
        novas_notas: List[str] = []
        novos_ids: List[str] = []

        # Lê as últimas 30 mensagens para performance e precisão
        inicio = max(0, total_elementos - 30)
        for i in range(inicio, total_elementos):
            try:
                elemento = locators_mensagens.nth(i)
                msg_id = self._gerar_id_mensagem(elemento)

                if msg_id in ids_processados_set:
                    continue

                texto = elemento.inner_text().strip()
                if not texto:
                    continue

                novos_ids.append(msg_id)

                numeros = extrair_numeros_notas(texto)
                for num in numeros:
                    foi_inserida = inserir_nota(numero_nota=num, origem_contato=nome_contato)
                    if foi_inserida:
                        print(f"  -> [NOVA NOTA CAPTURADA] Nº {num} ({nome_contato}) enfileirada no banco local.")
                        novas_notas.append(num)

            except Exception:
                continue

        return novas_notas, novos_ids

    def ler_mensagens_e_enfileirar(self) -> Tuple[int, List[str]]:
        """
        Varre TODAS as conversas com mensagens novas/não lidas (Multi-Lojas).
        Se várias lojas enviarem mensagens simultaneamente, abre cada uma
        e enfileira todas as notas no SQLite.
        """
        checkpoint = self._carregar_dados_checkpoint()

        # 1. Se for a primeira execução de todas, define o marco zero
        if not checkpoint:
            nome_contato = self.selecionar_chat_alvo()
            locators = self.page.locator("#main div.message-in, #main div.message-out, div.message-in, div.message-out")
            ids_iniciais = []
            for i in range(locators.count()):
                ids_iniciais.append(self._gerar_id_mensagem(locators.nth(i)))

            ultimo_id = ids_iniciais[-1] if ids_iniciais else ""
            novo_cp = {
                "criado_em": time.time(),
                "ultimo_id_processado": ultimo_id,
                "ids_processados": ids_iniciais[-300:],
            }
            self._salvar_dados_checkpoint(novo_cp)
            print("[WhatsApp] 🚀 Marco zero definido! Mensagens antigas ignoradas.")
            return 0, []

        todas_novas_notas: List[str] = []
        todos_novos_ids: List[str] = []

        # 2. Se houver um chat alvo fixo no .env, foca nele
        if WHATSAPP_CHAT_ALVO:
            nome_contato = self.selecionar_chat_alvo(WHATSAPP_CHAT_ALVO)
            novas, ids = self._extrair_notas_do_chat_aberto(nome_contato, checkpoint)
            todas_novas_notas.extend(novas)
            todos_novos_ids.extend(ids)
        else:
            # 3. VARREDURA MULTI-LOJAS: Percorre todas as conversas com mensagens não lidas
            seletor_nao_lidos = (
                "#pane-side div[role='listitem']:has(span[aria-label*='não lida']), "
                "#pane-side div[role='listitem']:has(span[aria-label*='unread']), "
                "#pane-side div[role='listitem']:has([data-icon*='unread']), "
                "#pane-side div[role='listitem']:has(span.x1rg5ohu)"
            )

            chats_visitados = 0
            max_chats = 15  # Limite de segurança por ciclo

            while chats_visitados < max_chats:
                nao_lidos = self.page.locator(seletor_nao_lidos)
                if nao_lidos.count() == 0:
                    break

                try:
                    # Clica na conversa com mensagem nova
                    item_chat = nao_lidos.first
                    item_chat.click()
                    time.sleep(1.2)
                    chats_visitados += 1

                    # Identifica o nome da loja/contato
                    header_span = self.page.locator("#main header span[title], #main header div[role='button'] span[dir='auto']")
                    nome_loja = "Loja_WhatsApp"
                    if header_span.count() > 0:
                        txt = header_span.first.inner_text().strip()
                        if txt:
                            nome_loja = txt

                    print(f"[WhatsApp Multi-Lojas] Verificando novas mensagens de: {nome_loja}...")
                    novas, ids = self._extrair_notas_do_chat_aberto(nome_loja, checkpoint)
                    todas_novas_notas.extend(novas)
                    todos_novos_ids.extend(ids)

                except Exception as err_chat:
                    print(f"[WhatsApp] Aviso ao processar conversa não lida: {err_chat}")
                    break

            # Se não havia nenhuma conversa com bolinha verde, lê a conversa aberta ou a do topo
            if chats_visitados == 0:
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
                self.selecionar_chat_alvo(origem_contato)
                time.sleep(1)

            print(f"[WhatsApp] Procurando mensagem da NF {numero_nota} para reagir com ✅...")
            bolhas = self.page.locator(f"#main div.message-in:has-text('{numero_nota}'), #main div.message-out:has-text('{numero_nota}'), div.message-in:has-text('{numero_nota}')")
            if bolhas.count() == 0:
                return False

            alvo = bolhas.last
            alvo.hover()
            time.sleep(0.5)

            btn_react = alvo.locator("button[aria-label='Reagir'], span[data-icon='react']")
            if btn_react.count() == 0:
                btn_react = self.page.locator("span[data-icon='react'], [data-testid='reaction-button']")

            if btn_react.count() > 0:
                btn_react.first.click()
                time.sleep(0.5)
                emoji_check = self.page.locator("button[aria-label*='marcado'], button[aria-label*='positivo'], [data-emoji='✅'], [data-emoji='👍']")
                if emoji_check.count() > 0:
                    emoji_check.first.click()
                    print(f"[WhatsApp] ✅ Reação adicionada com sucesso na mensagem da NF {numero_nota}!")
                    return True

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
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
